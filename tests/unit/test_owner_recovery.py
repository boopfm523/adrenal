from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from healthcurve.cli import main
from healthcurve.config import Environment
from healthcurve.identity.models import Owner
from healthcurve.identity.recovery import OwnerRecoveryError, recover_owner_access
from healthcurve.operations.audit import AuditAction

PASSWORD = "synthetic-new-password"


def _owner(*, mfa_enabled: bool = False) -> Owner:
    return Owner(
        id=uuid.uuid4(),
        email="old@example.test",
        password_hash="old-hash",
        display_name="Preserved Name",
        default_timezone="America/New_York",
        mfa_enabled=mfa_enabled,
        failed_login_count=4,
        locked_until=datetime(2026, 8, 10, tzinfo=UTC),
    )


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PROD])
def test_recovery_fails_closed_outside_development(environment: Environment) -> None:
    session = MagicMock()

    with pytest.raises(OwnerRecoveryError, match="only in development"):
        recover_owner_access(
            session,
            _owner(),
            environment=environment,
            new_email="new@example.test",
            new_password=PASSWORD,
        )

    session.scalar.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("mfa_enabled", "recovery_code_count"),
    [(True, 0), (False, 1)],
)
def test_recovery_refuses_any_mfa_state(mfa_enabled: bool, recovery_code_count: int) -> None:
    session = MagicMock()
    session.scalar.return_value = recovery_code_count
    owner = _owner(mfa_enabled=mfa_enabled)
    original = (owner.email, owner.password_hash)

    with pytest.raises(OwnerRecoveryError, match="disabled after MFA enrollment"):
        recover_owner_access(
            session,
            owner,
            environment=Environment.DEV,
            new_email="new@example.test",
            new_password=PASSWORD,
        )

    assert (owner.email, owner.password_hash) == original
    session.add.assert_not_called()


def test_recovery_updates_identity_only_revokes_sessions_and_audits_without_values() -> None:
    session = MagicMock()
    session.scalar.return_value = 0
    owner = _owner()
    preserved = (owner.id, owner.display_name, owner.default_timezone, owner.created_at)
    recovery_time = datetime(2026, 8, 10, 12, tzinfo=UTC)

    with (
        patch("healthcurve.identity.recovery.auth.hash_password", return_value="new-hash"),
        patch("healthcurve.identity.recovery.auth.revoke_all_sessions", return_value=2) as revoke,
        patch("healthcurve.identity.recovery.audit.record") as record,
    ):
        revoked = recover_owner_access(
            session,
            owner,
            environment=Environment.DEV,
            new_email=" NEW@Example.com ",
            new_password=PASSWORD,
            now=recovery_time,
        )

    assert revoked == 2
    assert owner.email == "new@example.com"
    assert owner.password_hash == "new-hash"
    assert owner.failed_login_count == 0
    assert owner.locked_until is None
    assert (owner.id, owner.display_name, owner.default_timezone, owner.created_at) == preserved
    revoke.assert_called_once_with(session, owner.id, now=recovery_time)
    record.assert_called_once()
    audit_call = record.call_args.kwargs
    assert audit_call["action"] is AuditAction.OWNER_ACCESS_RECOVERED
    assert audit_call["target_id"] == owner.id
    assert PASSWORD not in str(audit_call)
    assert "new@example.com" not in str(audit_call)


def test_recovery_cli_has_no_password_argument() -> None:
    with pytest.raises(SystemExit):
        main(["recover-owner-access", "--password", PASSWORD])
