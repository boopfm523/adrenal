from collections.abc import Iterator

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from healthcurve.operations.rate_limit import RateLimiter, RateLimitExceeded, RateLimitPolicy

pytestmark = [pytest.mark.slow]


@pytest.fixture(scope="module")
def redis_url() -> Iterator[str]:
    container = (
        DockerContainer("redis:7-alpine")
        .with_exposed_ports(6379)
        .waiting_for(LogMessageWaitStrategy("Ready to accept connections"))
    )
    with container as running:
        port = running.get_exposed_port(6379)
        yield f"redis://127.0.0.1:{port}/0"


def test_limit_is_shared_and_survives_application_restart(redis_url: str) -> None:
    policy = RateLimitPolicy(limit=2, window_seconds=60)

    before_restart = RateLimiter(redis_url)
    assert before_restart.check("report", "owner-restart-proof", policy).remaining == 1

    # A new limiter owns a new connection pool, as a restarted API/worker would.
    after_restart = RateLimiter(redis_url)
    assert after_restart.check("report", "owner-restart-proof", policy).remaining == 0

    another_process = RateLimiter(redis_url)
    with pytest.raises(RateLimitExceeded):
        another_process.check("report", "owner-restart-proof", policy)
