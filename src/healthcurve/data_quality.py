"""Deterministic, owner-scoped data-quality findings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.integrations.garmin.models import GarminImportBatch
from healthcurve.labs.models import LabDocument, LabDocumentStatus
from healthcurve.operations.jobs import dead_letters


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    finding_kind: Literal["problem", "genuine_absence"]
    severity: Literal["attention", "warning"]
    source: str
    title: str
    detail: str
    record_id: uuid.UUID | None
    href: str
    action_label: str


def findings_for_owner(session: Session, owner_id: uuid.UUID) -> list[Finding]:
    findings: list[Finding] = []
    drafts = session.scalars(
        select(ExtractionDraft).where(
            ExtractionDraft.owner_id == owner_id,
            ExtractionDraft.state.in_((DraftState.PENDING, DraftState.EDITED)),
        )
    )
    for draft in drafts:
        flag_set: set[str] = set()
        for candidate in draft.candidates:
            candidate_flags = candidate.get("flags")
            if isinstance(candidate_flags, list):
                flag_set.update(flag for flag in candidate_flags if isinstance(flag, str))
        flags = sorted(flag_set)
        if not flags:
            continue
        duplicate = "possible_duplicate" in flags
        findings.append(
            Finding(
                id=f"draft:{draft.id}",
                finding_kind="problem",
                severity="warning" if duplicate else "attention",
                source="AI extraction draft",
                title="Possible duplicate draft" if duplicate else "Draft needs clarification",
                detail=f"Review required for: {', '.join(flags)}.",
                record_id=draft.id,
                href="/data-quality#drafts",
                action_label="Review draft details",
            )
        )

    documents = session.scalars(
        select(LabDocument).where(
            LabDocument.owner_id == owner_id,
            LabDocument.status == LabDocumentStatus.REJECTED,
        )
    )
    for document in documents:
        findings.append(
            Finding(
                id=f"lab-document:{document.id}",
                finding_kind="problem",
                severity="warning",
                source="Lab document import",
                title="Lab document import failed",
                detail=f"Validation reason: {document.rejection_reason or 'unknown_failure'}.",
                record_id=document.id,
                href=f"/health-data?document={document.id}",
                action_label="Review or replace document",
            )
        )

    latest_garmin = session.scalar(
        select(GarminImportBatch)
        .where(GarminImportBatch.owner_id == owner_id)
        .order_by(GarminImportBatch.confirmed_at.desc())
        .limit(1)
    )
    if latest_garmin is not None:
        for metric in sorted(latest_garmin.missing_metrics):
            findings.append(
                Finding(
                    id=f"garmin-absence:{latest_garmin.id}:{metric}",
                    finding_kind="genuine_absence",
                    severity="attention",
                    source="Latest Garmin import",
                    title=f"{metric.replace('_', ' ').title()} not supplied",
                    detail="The provider source did not contain this metric; no zero is inferred.",
                    record_id=latest_garmin.id,
                    href="/settings#integration-heading",
                    action_label="Review Garmin settings",
                )
            )

    for job in dead_letters(session):
        findings.append(
            Finding(
                id=f"dead-letter:{job.id}",
                finding_kind="problem",
                severity="warning",
                source="Background job queue",
                title="Background task exhausted retries",
                detail=f"Task {job.task}; reason {job.last_error_code or 'unknown_error'}.",
                record_id=job.id,
                href="/data-quality#operations",
                action_label="Review operations runbook",
            )
        )
    return sorted(findings, key=lambda finding: (finding.finding_kind, finding.source, finding.id))
