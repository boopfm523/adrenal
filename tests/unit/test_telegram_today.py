from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import cast
from unittest.mock import ANY, MagicMock

import pytest
from sqlalchemy.orm import Session

from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import handlers
from healthcurve.medications import service as meds
from healthcurve.medications.models import DoseUnit, Route

OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _local_datetime(hour: int, minute: int = 0) -> datetime:
    """Build the timezone-naive local wall time used by event storage."""
    return datetime(2026, 8, 9, hour, minute, tzinfo=UTC).replace(tzinfo=None)


def _owner(timezone: str = "America/New_York") -> Owner:
    return Owner(
        id=OWNER_ID,
        email="owner@example.test",
        password_hash="synthetic-not-a-real-hash",
        default_timezone=timezone,
    )


def _slot(
    number: int,
    *,
    medication: str,
    status: str,
    scheduled: time | None = None,
    actual: datetime | None = None,
    planned: str | None = None,
    taken: str | None = None,
) -> meds.SlotComparison:
    recorded = actual is not None
    return meds.SlotComparison(
        slot_id=None if scheduled is None else uuid.UUID(f"00000000-0000-4000-8000-{number:012d}"),
        medication_id=uuid.UUID(f"10000000-0000-4000-8000-{number:012d}"),
        medication_name=medication,
        scheduled_local_time=scheduled,
        planned_amount=Decimal(planned) if planned is not None else None,
        actual_amount=Decimal(taken) if taken is not None else None,
        actual_local_time=actual,
        dose_id=uuid.UUID(f"20000000-0000-4000-8000-{number:012d}") if recorded else None,
        status=status,
        minutes_from_scheduled=None,
        unit=DoseUnit.MG,
        route=Route.ORAL,
    )


def _comparison(slots: list[meds.SlotComparison]) -> dict[str, object]:
    return {
        "slots": slots,
        "planned_total": Decimal("17"),
        "actual_total": Decimal("50"),
        "missed_slots": 3,
    }


def _action_lines(reply: handlers.Reply) -> list[str]:
    return [line.strip() for line in reply.text.splitlines() if line.startswith("  [")]


def _display_times(reply: handlers.Reply) -> list[str]:
    return [
        match.group() for line in _action_lines(reply) if (match := re.search(r"\d{2}:\d{2}", line))
    ]


def _today(owner: Owner, *, now: datetime) -> handlers.Reply:
    return handlers.handle_message(
        cast(Session, MagicMock(spec=Session)), owner, text="/today", now=now
    )


def test_today_interleaves_mixed_rows_by_displayed_experienced_or_scheduled_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = date(2026, 8, 9)
    slots = [
        _slot(
            1,
            medication="Hydrocortisone",
            status="late",
            scheduled=time(7),
            actual=_local_datetime(19, 16),
            planned="15",
            taken="15",
        ),
        _slot(2, medication="Fludrocortisone", status="missing", scheduled=time(7), planned="0.1"),
        _slot(
            3,
            medication="Hydrocortisone",
            status="missing",
            scheduled=time(10, 51),
            planned="15",
        ),
        _slot(4, medication="Hydrocortisone", status="missing", scheduled=time(17), planned="2.5"),
        _slot(
            5,
            medication="Hydrocortisone",
            status="unplanned",
            actual=_local_datetime(9, 49),
            taken="15",
        ),
        _slot(
            6,
            medication="Hydrocortisone",
            status="unplanned",
            actual=_local_datetime(21, 8),
            taken="5",
        ),
    ]
    compare = MagicMock(return_value=_comparison(slots))
    monkeypatch.setattr(handlers.meds, "compare_day", compare)

    reply = _today(_owner(), now=datetime(2026, 8, 10, 1, 30, tzinfo=UTC))

    assert _display_times(reply) == [
        "07:00",
        "09:49",
        "10:51",
        "17:00",
        "19:16",
        "21:08",
    ]
    assert "Taken: 50 of planned 17" in reply.text
    assert "3 scheduled dose(s) not recorded." in reply.text
    assert "missed" not in reply.text
    compare.assert_called_once_with(ANY, owner_id=OWNER_ID, day=day, timezone="America/New_York")


def test_today_uses_local_day_across_utc_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    compare = MagicMock(return_value=_comparison([]))
    monkeypatch.setattr(handlers.meds, "compare_day", compare)

    reply = _today(
        _owner("America/Los_Angeles"),
        now=datetime(2026, 8, 10, 0, 30, tzinfo=UTC),
    )

    assert reply.text.startswith("Today (2026-08-09, America/Los_Angeles)")
    compare.assert_called_once_with(
        ANY,
        owner_id=OWNER_ID,
        day=date(2026, 8, 9),
        timezone="America/Los_Angeles",
    )


def test_today_equal_times_have_deterministic_fact_then_absence_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    same_time = _local_datetime(7)
    slots = [
        _slot(4, medication="Zeta", status="missing", scheduled=time(7), planned="1"),
        _slot(3, medication="Zeta", status="unplanned", actual=same_time, taken="1"),
        _slot(2, medication="Alpha", status="unplanned", actual=same_time, taken="1"),
        _slot(1, medication="Alpha", status="missing", scheduled=time(7), planned="1"),
    ]
    monkeypatch.setattr(handlers.meds, "compare_day", MagicMock(return_value=_comparison(slots)))

    reply = _today(_owner(), now=datetime(2026, 8, 9, 12, tzinfo=UTC))

    assert _action_lines(reply) == [
        "[+] 07:00  Alpha 1 - extra",
        "[+] 07:00  Zeta 1 - extra",
        "[ ] 07:00  Alpha 1 - not recorded",
        "[ ] 07:00  Zeta 1 - not recorded",
    ]


def test_today_with_no_plan_still_orders_recorded_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    slots = [
        _slot(
            2,
            medication="Synthetic B",
            status="unplanned",
            actual=_local_datetime(20),
            taken="2",
        ),
        _slot(
            1,
            medication="Synthetic A",
            status="unplanned",
            actual=_local_datetime(6),
            taken="1",
        ),
    ]
    comparison = _comparison(slots) | {
        "planned_total": None,
        "actual_total": Decimal("3"),
        "missed_slots": 0,
    }
    monkeypatch.setattr(handlers.meds, "compare_day", MagicMock(return_value=comparison))

    reply = _today(_owner(), now=datetime(2026, 8, 9, 12, tzinfo=UTC))

    assert _display_times(reply) == ["06:00", "20:00"]
    assert "Taken: 3" in reply.text
    assert "of planned" not in reply.text


def test_today_with_no_plan_and_no_records_keeps_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison = _comparison([]) | {
        "planned_total": None,
        "actual_total": Decimal(0),
        "missed_slots": 0,
    }
    monkeypatch.setattr(handlers.meds, "compare_day", MagicMock(return_value=comparison))

    reply = _today(_owner(), now=datetime(2026, 8, 9, 12, tzinfo=UTC))

    assert "Nothing recorded, and no approved plan for today." in reply.text
    assert "Taken: 0" in reply.text
    assert _action_lines(reply) == []
