from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import pytest

from healthcurve.integrations.garmin.models import (
    GarminSyncOrigin,
    GarminSyncRun,
    GarminSyncStatus,
)
from healthcurve.public_site.exporter import (
    PUBLIC_SCHEMA_VERSION,
    PublicIds,
    eligibility_cutoff,
    project_public,
    sync_qualifies,
    validate_public_payload,
)

OWNER_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
RUN_ID = uuid.UUID("20000000-0000-0000-0000-000000000002")


def sync_run(**changes: object) -> GarminSyncRun:
    values: dict[str, object] = {
        "id": RUN_ID,
        "owner_id": OWNER_ID,
        "requested_start_date": date(2026, 8, 22),
        "requested_end_date": date(2026, 8, 23),
        "timezone": "America/New_York",
        "origin": GarminSyncOrigin.SCHEDULED,
        "status": GarminSyncStatus.COMPLETED,
        "started_at": datetime(2026, 8, 24, 16, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 8, 24, 16, 5, tzinfo=UTC),
        "counts": {"metrics": 10},
        "warning_codes": [],
        "client_version": "synthetic-test",
    }
    values.update(changes)
    return GarminSyncRun(**values)


def test_cutoff_is_local_noon_after_day_and_respects_dst() -> None:
    cutoff = eligibility_cutoff(date(2026, 3, 7), "America/New_York")
    assert cutoff.isoformat() == "2026-03-08T12:00:00-04:00"


def test_completed_covering_sync_after_cutoff_qualifies() -> None:
    run = sync_run()
    assert sync_qualifies(
        run,
        owner_id=OWNER_ID,
        day=date(2026, 8, 23),
        timezone="America/New_York",
        now=datetime(2026, 8, 24, 17, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("changes", "day", "timezone", "now"),
    [
        (
            {"status": GarminSyncStatus.RUNNING},
            date(2026, 8, 23),
            "America/New_York",
            datetime(2026, 8, 24, 17, tzinfo=UTC),
        ),
        (
            {"requested_end_date": date(2026, 8, 22)},
            date(2026, 8, 23),
            "America/New_York",
            datetime(2026, 8, 24, 17, tzinfo=UTC),
        ),
        (
            {"timezone": "UTC"},
            date(2026, 8, 23),
            "America/New_York",
            datetime(2026, 8, 24, 17, tzinfo=UTC),
        ),
        (
            {"finished_at": datetime(2026, 8, 24, 15, 59, tzinfo=UTC)},
            date(2026, 8, 23),
            "America/New_York",
            datetime(2026, 8, 24, 17, tzinfo=UTC),
        ),
        ({}, date(2026, 8, 23), "America/New_York", datetime(2026, 8, 24, 15, 59, tzinfo=UTC)),
    ],
)
def test_incomplete_or_stale_sync_fails_closed(
    changes: dict[str, object], day: date, timezone: str, now: datetime
) -> None:
    assert not sync_qualifies(
        sync_run(**changes),
        owner_id=OWNER_ID,
        day=day,
        timezone=timezone,
        now=now,
    )


def test_projection_replaces_private_identifiers_and_omits_unlisted_fields() -> None:
    private_id = "30000000-0000-0000-0000-000000000003"
    projected = project_public(
        {
            "dose_event_id": private_id,
            "occurred_at": "2026-08-23T10:00:00Z",
            "notes": "must not leave",
        },
        {"dose_event_id": True, "occurred_at": True},
        PublicIds(),
    )
    encoded = json.dumps(projected)
    assert projected == {"dose_event_id": "dose-1", "occurred_at": "2026-08-23T10:00:00Z"}
    assert private_id not in encoded
    assert "notes" not in encoded


def test_public_payload_validator_rejects_forbidden_keys() -> None:
    with pytest.raises(ValueError, match="forbidden keys"):
        validate_public_payload(
            {"schema_version": PUBLIC_SCHEMA_VERSION, "curve": {"notes": "synthetic"}}
        )


def test_public_payload_validator_rejects_uuid_shaped_values() -> None:
    with pytest.raises(ValueError, match="UUID-shaped"):
        validate_public_payload(
            {
                "schema_version": PUBLIC_SCHEMA_VERSION,
                "id": "30000000-0000-4000-8000-000000000003",
            }
        )


def test_public_payload_validator_accepts_allowlisted_shape() -> None:
    validate_public_payload(
        {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "date": "2026-08-23",
            "curve": {"series_name": "Synthetic curve"},
        }
    )
