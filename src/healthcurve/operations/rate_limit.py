"""Durable, privacy-preserving abuse limits backed by Redis.

Counters live in Redis rather than process memory, so multiple API/worker processes
share one limit and a process restart cannot reset it. Identifiers are one-way hashed
before becoming Redis keys; email addresses and health content never enter Redis.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from redis import Redis
from redis.exceptions import RedisError

from healthcurve.logging import get_logger

log = get_logger(__name__)

_FIXED_WINDOW: Final = """
local current = redis.call('INCRBY', KEYS[1], ARGV[3])
if current == tonumber(ARGV[3]) then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  ttl = tonumber(ARGV[2])
end
return {current, ttl}
"""


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitResult:
    limit: int
    remaining: int
    retry_after: int

    @property
    def headers(self) -> dict[str, str]:
        return {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.retry_after),
        }


class RateLimitExceeded(RuntimeError):
    def __init__(self, result: RateLimitResult) -> None:
        super().__init__("rate limit exceeded")
        self.result = result


class RateLimitUnavailable(RuntimeError):
    """Redis could not make a durable rate-limit decision."""


class RateLimiter:
    """Atomic fixed-window limiter.

    A missing URL disables limits only for local development and tests. Production
    configuration validation requires Redis, so deployed paths always fail closed if
    Redis is unavailable rather than silently accepting unlimited expensive work.
    """

    def __init__(self, redis_url: str | None) -> None:
        self.enabled = redis_url is not None
        self._redis: Redis | None = (
            Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
            if redis_url
            else None
        )

    @staticmethod
    def _key(scope: str, identity: str) -> str:
        digest = hashlib.sha256(identity.strip().casefold().encode()).hexdigest()
        return f"hc:rate:{scope}:{digest}"

    def check(
        self, scope: str, identity: str, policy: RateLimitPolicy, *, cost: int = 1
    ) -> RateLimitResult:
        if cost < 1:
            raise ValueError("rate-limit cost must be positive")
        if self._redis is None:
            return RateLimitResult(policy.limit, policy.limit, 0)
        try:
            raw = self._redis.eval(
                _FIXED_WINDOW,
                1,
                self._key(scope, identity),
                policy.limit,
                policy.window_seconds,
                cost,
            )
            count, ttl = int(raw[0]), max(1, int(raw[1]))
        except (RedisError, TypeError, ValueError, IndexError) as exc:
            log.error(
                "rate limit decision unavailable",
                outcome="failed",
                reason_code=type(exc).__name__,
            )
            raise RateLimitUnavailable from exc

        result = RateLimitResult(
            limit=policy.limit,
            remaining=max(0, policy.limit - count),
            retry_after=ttl,
        )
        if count > policy.limit:
            log.warning(
                "rate limit exceeded",
                outcome="rejected",
                reason_code=scope,
            )
            raise RateLimitExceeded(result)
        return result
