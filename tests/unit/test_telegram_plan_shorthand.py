from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from healthcurve.ai.models import ExtractionDraft
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import handlers
from healthcurve.medications.models import (
    DoseTimingMode,
    DoseUnit,
    Medication,
    RegimenDoseSlot,
    Route,
)


def _owner() -> Owner:
    return Owner(
        id=uuid.uuid4(),
        email="plan-shorthand@example.test",
        password_hash="synthetic-not-a-real-hash",  # pragma: allowlist secret
        default_timezone="America/New_York",
    )


def _medication(name: str, normalized_name: str) -> Medication:
    return Medication(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        name=name,
        normalized_name=normalized_name,
        default_unit=DoseUnit.MG,
        default_route=Route.ORAL,
    )


def _slot(
    medication: Medication,
    amount: str,
    *,
    clock: time | None,
    sort_order: int,
    condition: str | None = None,
) -> RegimenDoseSlot:
    return RegimenDoseSlot(
        id=uuid.uuid4(),
        regimen_version_id=uuid.uuid4(),
        medication_id=medication.id,
        medication=medication,
        timing_mode=DoseTimingMode.WAKE if clock is None else DoseTimingMode.FIXED_TIME,
        scheduled_local_time=clock,
        reminder_local_time=time(7, 30) if clock is None else None,
        amount=Decimal(amount),
        unit=DoseUnit.MG,
        route=Route.ORAL,
        condition=condition,
        sort_order=sort_order,
    )


def _resolve(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    *,
    sent_at: datetime,
    slots: list[RegimenDoseSlot] | None,
) -> tuple[handlers.Reply | None, list[Any], MagicMock]:
    active = MagicMock(return_value=None if slots is None else SimpleNamespace(slots=slots))
    monkeypatch.setattr(handlers.meds, "active_version_at", active)
    captured: list[Any] = []

    def fake_store(
        session: Session,
        owner: Owner,
        candidates: list[Any],
        **kwargs: Any,
    ) -> ExtractionDraft:
        del session, owner, kwargs
        captured.extend(candidates)
        return cast(ExtractionDraft, SimpleNamespace(id=uuid.uuid4()))

    monkeypatch.setattr(handlers, "_store_draft", fake_store)

    def fake_draft_reply(draft: ExtractionDraft, candidates: list[Any]) -> handlers.Reply:
        del draft, candidates
        return handlers.Reply("confirmation draft")

    monkeypatch.setattr(handlers, "_draft_reply", fake_draft_reply)
    reply = handlers._planned_dose_draft(  # pyright: ignore[reportPrivateUsage]
        cast(Session, MagicMock(spec=Session)), _owner(), text, now=sent_at
    )
    return reply, captured, active


def test_generic_morning_shorthand_resolves_all_unique_plan_medications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydrocortisone = _medication("Hydrocortisone tablet", "hydrocortisone tablet")
    fludrocortisone = _medication("Fludrocortisone", "fludrocortisone")
    sent_at = datetime(2026, 8, 23, 11, 15, tzinfo=UTC)  # 07:15 EDT

    reply, candidates, active = _resolve(
        monkeypatch,
        "I took my regular doses.",
        sent_at=sent_at,
        slots=[
            _slot(hydrocortisone, "15", clock=None, sort_order=0),
            _slot(fludrocortisone, "0.1", clock=time(7), sort_order=1),
            _slot(hydrocortisone, "5", clock=time(14), sort_order=2),
        ],
    )

    assert reply is not None and reply.text == "confirmation draft"
    assert [(candidate.medication_name, candidate.amount) for candidate in candidates] == [
        ("Hydrocortisone tablet", Decimal("15")),
        ("Fludrocortisone", Decimal("0.1")),
    ]
    assert all(
        candidate.local_time.isoformat() == "2026-08-23T07:15:00" for candidate in candidates
    )
    assert active.call_count == 1
    assert active.call_args.args[2] == sent_at


def test_named_afternoon_shorthand_selects_only_named_medication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydrocortisone = _medication("Hydrocortisone tablet", "hydrocortisone tablet")
    other = _medication("Synthetic afternoon medicine", "synthetic afternoon medicine")
    reply, candidates, _ = _resolve(
        monkeypatch,
        "I took my afternoon hydrocortisone.",
        sent_at=datetime(2026, 8, 23, 18, 5, tzinfo=UTC),  # 14:05 EDT
        slots=[
            _slot(hydrocortisone, "5", clock=time(14), sort_order=1),
            _slot(other, "2", clock=time(14), sort_order=2),
        ],
    )

    assert reply is not None and reply.text == "confirmation draft"
    assert len(candidates) == 1
    assert candidates[0].medication_name == "Hydrocortisone tablet"
    assert candidates[0].amount == Decimal("5")


def test_evening_wording_supports_a_future_three_times_daily_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydrocortisone = _medication("Hydrocortisone tablet", "hydrocortisone tablet")
    reply, candidates, _ = _resolve(
        monkeypatch,
        "I took my evening dose.",
        sent_at=datetime(2026, 8, 23, 23, 30, tzinfo=UTC),  # 19:30 EDT
        slots=[_slot(hydrocortisone, "2.5", clock=time(19), sort_order=3)],
    )

    assert reply is not None and reply.text == "confirmation draft"
    assert len(candidates) == 1
    assert candidates[0].amount == Decimal("2.5")


def test_no_approved_plan_at_sent_time_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    reply, candidates, active = _resolve(
        monkeypatch,
        "I took my scheduled medicine.",
        sent_at=datetime(2026, 8, 23, 11, 15, tzinfo=UTC),
        slots=None,
    )

    assert reply is not None
    assert "couldn't find an approved medication plan" in reply.text
    assert "Nothing was recorded" in reply.text
    assert candidates == []
    assert active.call_args.args[2] == datetime(2026, 8, 23, 11, 15, tzinfo=UTC)


def test_multiple_matching_slots_for_one_medication_requires_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydrocortisone = _medication("Hydrocortisone tablet", "hydrocortisone tablet")
    reply, candidates, _ = _resolve(
        monkeypatch,
        "I took my afternoon dose.",
        sent_at=datetime(2026, 8, 23, 18, 5, tzinfo=UTC),
        slots=[
            _slot(hydrocortisone, "5", clock=time(13), sort_order=1),
            _slot(hydrocortisone, "2.5", clock=time(16), sort_order=2),
        ],
    )

    assert reply is not None
    assert "more than one afternoon slot" in reply.text
    assert "Nothing was recorded" in reply.text
    assert candidates == []


def test_unknown_named_medication_does_not_fall_back_to_other_plan_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hydrocortisone = _medication("Hydrocortisone tablet", "hydrocortisone tablet")
    reply, candidates, _ = _resolve(
        monkeypatch,
        "I took my afternoon fludrocortisone.",
        sent_at=datetime(2026, 8, 23, 18, 5, tzinfo=UTC),
        slots=[_slot(hydrocortisone, "5", clock=time(14), sort_order=1)],
    )

    assert reply is not None
    assert "does not contain the named medication" in reply.text
    assert candidates == []


def test_non_dose_statement_is_not_intercepted(monkeypatch: pytest.MonkeyPatch) -> None:
    reply, candidates, active = _resolve(
        monkeypatch,
        "I took a walk.",
        sent_at=datetime(2026, 8, 23, 11, 15, tzinfo=UTC),
        slots=[],
    )

    assert reply is None
    assert candidates == []
    active.assert_not_called()


@pytest.mark.parametrize(
    "text",
    (
        "I took a 5 mg stress dose of hydrocortisone.",
        "I took an emergency hydrocortisone injection.",
        "I took 15mg hydrocortisone.",
    ),
)
def test_explicit_or_non_scheduled_doses_continue_to_existing_parser(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    reply, candidates, active = _resolve(
        monkeypatch,
        text,
        sent_at=datetime(2026, 8, 23, 11, 15, tzinfo=UTC),
        slots=[],
    )

    assert reply is None
    assert candidates == []
    active.assert_not_called()
