from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from healthcurve.cli import main
from healthcurve.identity.models import Owner
from healthcurve.selective_test_data_cleanup import (
    BlockingReferences,
    PreservedCounts,
    ResetCounts,
    SelectiveResetPreview,
)


def _preview(*, blocker: int = 0) -> SelectiveResetPreview:
    identifiers = [uuid.uuid4() for _ in range(7)]
    return SelectiveResetPreview(
        regimen_version_ids=(identifiers[0],),
        regimen_dose_slot_ids=(identifiers[1],),
        approved_instruction_ids=(identifiers[2],),
        dose_reminder_ids=(identifiers[3],),
        dose_event_ids=(identifiers[4],),
        stress_episode_ids=(identifiers[5],),
        symptom_event_ids=(identifiers[6],),
        counts=ResetCounts(1, 1, 1, 1, 1, 1, 1),
        preserved=PreservedCounts(3, 0, 2, 1, 4, 5, 1, 900, 3, 2, 8, 1, 1, 4),
        blockers=BlockingReferences(blocker),
        confirmation_phrase="CLEAR DECLARED TEST DATA ABCDEF123456",
    )


def _factory(owner: Owner) -> tuple[MagicMock, MagicMock]:
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = owner
    session.begin.return_value.__enter__.return_value = session
    factory = MagicMock(return_value=session)
    return factory, session


def test_reset_is_preview_only_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    preview = _preview()
    owner = Owner(id=uuid.uuid4(), email="synthetic@example.test", password_hash="unused")
    factory, _session = _factory(owner)
    with (
        patch("healthcurve.cli.get_session_factory", return_value=factory),
        patch(
            "healthcurve.selective_test_data_cleanup.preview_selective_test_data_reset",
            return_value=preview,
        ),
        patch(
            "healthcurve.selective_test_data_cleanup.execute_selective_test_data_reset"
        ) as execute,
        patch("builtins.input") as ask,
    ):
        assert main(["reset-declared-test-data"]) == 0

    output = capsys.readouterr().out
    assert "WILL CLEAR" in output
    assert "fact.dose_event=1" in output
    assert "WILL PRESERVE" in output
    assert "fact.blood_pressure_event=4" in output
    assert "fact.garmin_metric_event=900" in output
    assert "Preview only; nothing changed" in output
    execute.assert_not_called()
    ask.assert_not_called()


def test_execute_requires_local_preview_bound_confirmation() -> None:
    preview = _preview()
    owner = Owner(id=uuid.uuid4(), email="synthetic@example.test", password_hash="unused")
    factory, session = _factory(owner)
    with (
        patch("healthcurve.cli.get_session_factory", return_value=factory),
        patch(
            "healthcurve.selective_test_data_cleanup.preview_selective_test_data_reset",
            return_value=preview,
        ),
        patch(
            "healthcurve.selective_test_data_cleanup.execute_selective_test_data_reset",
            return_value=preview.counts,
        ) as execute,
        patch("builtins.input", return_value=preview.confirmation_phrase) as ask,
    ):
        assert main(["reset-declared-test-data", "--execute"]) == 0

    ask.assert_called_once()
    execute.assert_called_once_with(
        session,
        owner_id=owner.id,
        preview=preview,
        confirmation=preview.confirmation_phrase,
    )


def test_execute_refuses_blocked_episode_relationship() -> None:
    preview = _preview(blocker=1)
    owner = Owner(id=uuid.uuid4(), email="synthetic@example.test", password_hash="unused")
    factory, _session = _factory(owner)
    with (
        patch("healthcurve.cli.get_session_factory", return_value=factory),
        patch(
            "healthcurve.selective_test_data_cleanup.preview_selective_test_data_reset",
            return_value=preview,
        ),
        patch(
            "healthcurve.selective_test_data_cleanup.execute_selective_test_data_reset"
        ) as execute,
        patch("builtins.input") as ask,
        pytest.raises(SystemExit, match="emergency injection"),
    ):
        main(["reset-declared-test-data", "--execute"])
    execute.assert_not_called()
    ask.assert_not_called()


def test_confirmation_cannot_be_supplied_on_command_line() -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "reset-declared-test-data",
                "--confirmation",
                "CLEAR DECLARED TEST DATA ABCDEF123456",
            ]
        )
