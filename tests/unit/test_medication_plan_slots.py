"""Validation boundaries for fixed-time and wake-anchored plan slots."""

import uuid
from datetime import time

import pytest
from pydantic import ValidationError

from healthcurve.api.schemas import DoseSlotIn
from healthcurve.medications.models import DoseTimingMode, DoseUnit, Route


def _slot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "medication_id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
        "scheduled_local_time": time(7),
        "amount": "10",
        "unit": DoseUnit.MG,
        "route": Route.ORAL,
    }
    value.update(overrides)
    return value


def test_fixed_slot_requires_only_a_scheduled_time() -> None:
    slot = DoseSlotIn.model_validate(_slot())

    assert slot.timing_mode is DoseTimingMode.FIXED_TIME
    assert slot.scheduled_local_time == time(7)
    assert slot.reminder_local_time is None


def test_wake_slot_requires_only_a_reminder_fallback() -> None:
    slot = DoseSlotIn.model_validate(
        _slot(
            timing_mode=DoseTimingMode.WAKE,
            scheduled_local_time=None,
            reminder_local_time=time(7, 30),
        )
    )

    assert slot.scheduled_local_time is None
    assert slot.reminder_local_time == time(7, 30)


@pytest.mark.parametrize(
    "value",
    (
        _slot(scheduled_local_time=None),
        _slot(reminder_local_time=time(7, 30)),
        _slot(timing_mode=DoseTimingMode.WAKE, reminder_local_time=time(7, 30)),
        _slot(
            timing_mode=DoseTimingMode.WAKE,
            scheduled_local_time=None,
            reminder_local_time=None,
        ),
    ),
)
def test_mixed_or_incomplete_timing_fields_are_rejected(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DoseSlotIn.model_validate(value)
