"""Privacy-safe command used by backup health checks and the operator runbook."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from healthcurve.db import get_session_factory
from healthcurve.operations.backup_jobs import backup_health


def main() -> int:
    directory_value = os.environ.get("HC_BACKUP_LOCAL_DIR", "").strip()
    if not directory_value:
        print(json.dumps({"state": "alert", "reason_codes": ["backup_configuration_incomplete"]}))
        return 1
    try:
        with get_session_factory()() as session:
            health = backup_health(session, Path(directory_value), now=datetime.now(UTC))
    except Exception:
        # Database and filesystem exceptions may contain credentials or owner paths.
        print(json.dumps({"state": "alert", "reason_codes": ["backup_status_unavailable"]}))
        return 1
    print(
        json.dumps(
            {
                "age_hours": round(health.age_hours, 3) if health.age_hours is not None else None,
                "dead_letter": health.dead_letter,
                "latest_job_error_code": health.latest_job_error_code,
                "latest_job_status": (
                    health.latest_job_status.value if health.latest_job_status else None
                ),
                "last_success_at": (
                    health.last_success_at.astimezone(UTC).isoformat()
                    if health.last_success_at
                    else None
                ),
                "protected_set_count": health.protected_set_count,
                "reason_codes": list(health.reason_codes),
                "state": health.state,
            },
            sort_keys=True,
        )
    )
    return 0 if health.state == "healthy" else 2


if __name__ == "__main__":
    sys.exit(main())
