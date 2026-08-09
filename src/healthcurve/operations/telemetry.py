"""Privacy-safe rolling operational event counters in Redis.

Only an allow-listed event type and server timestamp are stored. There are no owner
identifiers, request paths, prompts, exception messages, or health values.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from redis import Redis
from redis.exceptions import RedisError

from healthcurve.logging import get_logger

log = get_logger(__name__)


class OperationalEvent(StrEnum):
    REQUEST_ERROR = "request_error"
    AUTH_FAILURE = "auth_failure"
    MODEL_FAILURE = "model_failure"


_RECORD: Final = """
local t = redis.call('TIME')
local score = tonumber(t[1]) + (tonumber(t[2]) / 1000000)
local sequence = redis.call('INCR', KEYS[2])
redis.call('ZADD', KEYS[1], score, tostring(t[1]) .. ':' .. tostring(t[2]) .. ':' .. sequence)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', score - tonumber(ARGV[1]))
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]) + 3600)
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[1]) + 3600)
return score
"""

RETENTION_SECONDS: Final = 7 * 24 * 60 * 60


class TelemetryUnavailable(RuntimeError):
    pass


class OperationalTelemetry:
    def __init__(self, redis_url: str | None) -> None:
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
    def _key(event: OperationalEvent) -> str:
        return f"hc:telemetry:{event.value}"

    def record(self, event: OperationalEvent) -> None:
        """Best-effort recording; observability must not break the observed request."""
        if self._redis is None:
            return
        try:
            self._redis.eval(
                _RECORD,
                2,
                self._key(event),
                "hc:telemetry:sequence",
                RETENTION_SECONDS,
            )
        except RedisError as exc:
            log.error(
                "operational telemetry unavailable",
                outcome="failed",
                reason_code=type(exc).__name__,
            )

    def count(self, event: OperationalEvent, *, window_seconds: int) -> int:
        if window_seconds < 1 or window_seconds > RETENTION_SECONDS:
            raise ValueError("telemetry window is outside retention")
        if self._redis is None:
            raise TelemetryUnavailable("Redis telemetry is not configured")
        try:
            now = self._redis.time()
            timestamp = int(now[0]) + (int(now[1]) / 1_000_000)
            value = self._redis.zcount(self._key(event), timestamp - window_seconds, "+inf")
            return int(value)
        except (RedisError, TypeError, ValueError, IndexError) as exc:
            raise TelemetryUnavailable from exc
