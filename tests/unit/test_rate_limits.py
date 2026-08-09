from pathlib import Path
from unittest import mock

import pytest
import yaml
from redis import Redis
from redis.exceptions import ConnectionError

from healthcurve.config import Environment, Settings
from healthcurve.operations.rate_limit import (
    RateLimiter,
    RateLimitExceeded,
    RateLimitPolicy,
    RateLimitUnavailable,
)


def limiter_with(client: mock.MagicMock) -> RateLimiter:
    with mock.patch.object(Redis, "from_url", return_value=client):
        return RateLimiter("redis://redis:6379/0")


def test_counter_is_atomic_observable_and_contains_no_raw_identity() -> None:
    client = mock.MagicMock()
    client.eval.side_effect = ([1, 60], [2, 59], [3, 58])
    limiter = limiter_with(client)
    policy = RateLimitPolicy(limit=2, window_seconds=60)

    first = limiter.check("login", "Owner@Example.com", policy)
    second = limiter.check("login", "owner@example.com", policy)

    assert first.headers == {
        "RateLimit-Limit": "2",
        "RateLimit-Remaining": "1",
        "RateLimit-Reset": "60",
    }
    assert second.remaining == 0
    with pytest.raises(RateLimitExceeded) as raised:
        limiter.check("login", "owner@example.com", policy)
    assert raised.value.result.retry_after == 58
    assert raised.value.result.remaining == 0

    calls = " ".join(str(call) for call in client.eval.call_args_list)
    assert "owner@example.com" not in calls.lower()
    assert "hc:rate:login:" in calls


def test_owner_scopes_get_distinct_hashed_keys_and_model_cost_is_counted() -> None:
    client = mock.MagicMock()
    client.eval.side_effect = ([2, 60], [1, 60])
    limiter = limiter_with(client)
    policy = RateLimitPolicy(limit=10, window_seconds=60)

    limiter.check("model", "owner-one", policy, cost=2)
    limiter.check("model", "owner-two", policy)

    first_args = client.eval.call_args_list[0].args
    second_args = client.eval.call_args_list[1].args
    assert first_args[2] != second_args[2]
    assert first_args[-1] == 2
    assert second_args[-1] == 1


def test_redis_failure_is_explicit_and_fails_closed() -> None:
    client = mock.MagicMock()
    client.eval.side_effect = ConnectionError("unavailable")
    limiter = limiter_with(client)

    with pytest.raises(RateLimitUnavailable):
        limiter.check("report", "opaque-owner-id", RateLimitPolicy(6, 3600))


def test_local_unconfigured_limiter_is_disabled_but_production_requires_redis() -> None:
    result = RateLimiter(None).check("login", "owner", RateLimitPolicy(5, 60))
    assert result.remaining == 5

    with pytest.raises(ValueError, match="HC_REDIS_URL"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment=Environment.PROD,
            ai_database_url="postgresql+psycopg://ai@postgres/healthcurve",
            database_url="postgresql+psycopg://app@postgres/healthcurve",
        )


def test_compose_redis_uses_aof_and_a_named_volume() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    redis = compose["services"]["redis"]

    assert "--appendonly" in redis["command"]
    assert redis["command"][redis["command"].index("--appendonly") + 1] == "yes"
    assert "redis_data:/data" in redis["volumes"]
    assert "redis_data" in compose["volumes"]
