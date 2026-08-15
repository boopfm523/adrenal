"""Audit trail (SAFE-28).

Audit entries record *that* something happened, never the health content of what
happened. ``target_id`` points at the affected record; anyone with the right to read
the audit log already has the right to read that record.

Class C13: audit survives ordinary deletion. Deleting the account removes the health
data but keeps the entries recording the deletion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from healthcurve.db import OPS_SCHEMA, OpsBase, StrEnumType

UNAUTHENTICATED_ACTOR = "unauthenticated"


class AuditAction(StrEnum):
    """Every safety-relevant action named by SAFE-28."""

    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    SESSION_REVOKED = "session_revoked"
    PASSWORD_CHANGED = "password_changed"  # noqa: S105 -- an action name, not a secret
    MFA_ENROLLED = "mfa_enrolled"
    MFA_REMOVED = "mfa_removed"
    MFA_RECOVERY_CODES_REGENERATED = "mfa_recovery_codes_regenerated"
    MFA_RECOVERY_CODE_USED = "mfa_recovery_code_used"
    OWNER_ACCESS_RECOVERED = "owner_access_recovered"

    REGIMEN_DRAFTED = "regimen_drafted"
    REGIMEN_DRAFT_UPDATED = "regimen_draft_updated"
    REGIMEN_DRAFT_DELETED = "regimen_draft_deleted"
    REGIMEN_DELETED = "regimen_deleted"
    SYNTHETIC_MEDICATION_BOOTSTRAP_PURGED = "synthetic_medication_bootstrap_purged"
    SELECTIVE_TEST_DATA_RESET = "selective_test_data_reset"
    REGIMEN_APPROVED = "regimen_approved"
    REGIMEN_HANDOFF = "regimen_handoff"
    REGIMEN_RETIRED = "regimen_retired"

    RECORD_CREATED = "record_created"
    RECORD_CORRECTED = "record_corrected"
    RECORD_DELETED = "record_deleted"

    EXPORT_REQUESTED = "export_requested"
    EXPORT_GENERATED = "export_generated"
    EXPORT_DOWNLOADED = "export_downloaded"
    REPORT_GENERATED = "report_generated"
    REPORT_DOWNLOADED = "report_downloaded"

    INTEGRATION_CONNECTED = "integration_connected"
    INTEGRATION_CREDENTIAL_UPDATED = "integration_credential_updated"
    INTEGRATION_CREDENTIAL_ROTATED = "integration_credential_rotated"
    INTEGRATION_DISCONNECTED = "integration_disconnected"
    INTEGRATION_IMPORT_CONFIRMED = "integration_import_confirmed"
    INTEGRATION_SYNC_COMPLETED = "integration_sync_completed"
    DATA_QUALITY_ACKNOWLEDGED = "data_quality_acknowledged"

    AI_ANALYSIS_GENERATED = "ai_analysis_generated"
    AI_ANALYSIS_DELETED = "ai_analysis_deleted"

    DATA_DELETED = "data_deleted"


class AuditEntry(OpsBase):
    __tablename__ = "audit_entry"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    #: Who. "owner:<uuid>", "system", "unauthenticated", or "telegram:<chat id>".
    #: Never null -- an action with no actor is not auditable. Unauthenticated login
    #: attempts deliberately do not contain the submitted email address.
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[AuditAction] = mapped_column(
        StrEnumType(AuditAction, 48), nullable=False, index=True
    )

    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[uuid.UUID | None] = mapped_column()

    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)

    #: Structural detail only: which fields changed, not their values. Putting a dose
    #: amount here would put health data in the audit log.
    change_summary: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (Index("ix_audit_action_time", "action", "occurred_at"), OPS_SCHEMA)


def record(
    session: Session,
    *,
    actor: str,
    action: AuditAction,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    change_summary: str | None = None,
) -> AuditEntry:
    """Write an audit entry in the caller's transaction.

    Deliberately part of the same transaction as the change it describes: an audited
    action that rolls back should not leave an entry claiming it happened.
    """
    entry = AuditEntry(
        occurred_at=datetime.now(UTC),
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        correlation_id=correlation_id,
        change_summary=change_summary,
    )
    session.add(entry)
    return entry


def actor_for_owner(owner_id: uuid.UUID) -> str:
    return f"owner:{owner_id}"
