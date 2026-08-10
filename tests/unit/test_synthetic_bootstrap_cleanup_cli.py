from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from healthcurve.cli import main
from healthcurve.config import Environment
from healthcurve.development_cleanup import (
    CleanupCounts,
    CleanupPreview,
    ReferenceCounts,
)
from healthcurve.identity.models import Owner


def _preview() -> CleanupPreview:
    regimen = uuid.UUID("11111111-1111-4111-8111-111111111111")
    slots = tuple(uuid.UUID(f"22222222-2222-4222-8222-{index:012d}") for index in range(1, 5))
    instructions = tuple(
        uuid.UUID(f"33333333-3333-4333-8333-{index:012d}") for index in range(1, 3)
    )
    medications = tuple(uuid.UUID(f"44444444-4444-4444-8444-{index:012d}") for index in range(1, 4))
    return CleanupPreview(
        profile_version="legacy-medications-template-v1",
        regimen_version_ids=(regimen,),
        regimen_dose_slot_ids=slots,
        approved_instruction_ids=instructions,
        medication_ids=medications,
        counts=CleanupCounts(1, 4, 2, 3),
        references=ReferenceCounts(),
        confirmation_phrase="PURGE SYNTHETIC BOOTSTRAP ABCDEF123456",
    )


def _factory(owner: Owner) -> MagicMock:
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = owner
    session.begin.return_value.__enter__.return_value = session
    return MagicMock(return_value=session)


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PROD])
def test_cleanup_cli_refuses_outside_development(environment: Environment) -> None:
    factory = MagicMock()
    with (
        patch(
            "healthcurve.cli.get_settings", return_value=SimpleNamespace(environment=environment)
        ),
        patch("healthcurve.cli.get_session_factory", factory),
        pytest.raises(SystemExit, match="only in development"),
    ):
        main(["purge-synthetic-medication-bootstrap"])
    factory.assert_not_called()


def test_cleanup_cli_is_preview_only_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    preview = _preview()
    owner = Owner(id=uuid.uuid4(), email="synthetic@example.test", password_hash="unused")
    factory = _factory(owner)
    with (
        patch(
            "healthcurve.cli.get_settings",
            return_value=SimpleNamespace(environment=Environment.DEV),
        ),
        patch("healthcurve.cli.get_session_factory", return_value=factory),
        patch(
            "healthcurve.development_cleanup.preview_synthetic_bootstrap",
            return_value=preview,
        ),
        patch("healthcurve.development_cleanup.execute_synthetic_bootstrap_cleanup") as execute,
        patch("builtins.input") as ask,
    ):
        assert main(["purge-synthetic-medication-bootstrap"]) == 0

    output = capsys.readouterr().out
    assert str(preview.regimen_version_ids[0]) in output
    assert "plan.regimen_version=1" in output
    assert "plan.regimen_dose_slot=4" in output
    assert preview.confirmation_phrase in output
    assert "Preview only; nothing changed" in output
    ask.assert_not_called()
    execute.assert_not_called()


def test_cleanup_cli_execute_requires_hidden_local_interaction(
    capsys: pytest.CaptureFixture[str],
) -> None:
    preview = _preview()
    owner = Owner(id=uuid.uuid4(), email="synthetic@example.test", password_hash="unused")
    factory = _factory(owner)
    with (
        patch(
            "healthcurve.cli.get_settings",
            return_value=SimpleNamespace(environment=Environment.DEV),
        ),
        patch("healthcurve.cli.get_session_factory", return_value=factory),
        patch(
            "healthcurve.development_cleanup.preview_synthetic_bootstrap",
            return_value=preview,
        ),
        patch(
            "healthcurve.development_cleanup.execute_synthetic_bootstrap_cleanup",
            return_value=preview.counts,
        ) as execute,
        patch("builtins.input", return_value=preview.confirmation_phrase) as ask,
    ):
        assert main(["purge-synthetic-medication-bootstrap", "--execute"]) == 0

    ask.assert_called_once()
    execute.assert_called_once_with(
        factory.return_value,
        owner_id=owner.id,
        preview=preview,
        confirmation=preview.confirmation_phrase,
    )
    assert "Purged exact legacy synthetic medication bootstrap" in capsys.readouterr().out


def test_cleanup_cli_never_prompts_or_executes_when_references_remain() -> None:
    preview = replace(_preview(), references=ReferenceCounts(doses=1))
    owner = Owner(id=uuid.uuid4(), email="synthetic@example.test", password_hash="unused")
    factory = _factory(owner)
    with (
        patch(
            "healthcurve.cli.get_settings",
            return_value=SimpleNamespace(environment=Environment.DEV),
        ),
        patch("healthcurve.cli.get_session_factory", return_value=factory),
        patch(
            "healthcurve.development_cleanup.preview_synthetic_bootstrap",
            return_value=preview,
        ),
        patch("healthcurve.development_cleanup.execute_synthetic_bootstrap_cleanup") as execute,
        patch("builtins.input") as ask,
        pytest.raises(SystemExit, match="blocked by retained references"),
    ):
        main(["purge-synthetic-medication-bootstrap", "--execute"])
    ask.assert_not_called()
    execute.assert_not_called()


def test_cleanup_cli_does_not_accept_confirmation_on_command_line() -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "purge-synthetic-medication-bootstrap",
                "--confirm",
                "PURGE SYNTHETIC BOOTSTRAP ABCDEF123456",
            ]
        )
