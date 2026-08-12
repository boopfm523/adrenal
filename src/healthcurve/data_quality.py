"""Deterministic, owner-scoped data-quality findings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.episodes.models import EpisodeStatus, StressEpisode
from healthcurve.integrations.garmin.models import (
    GarminConnection,
    GarminConnectionState,
    GarminImportBatch,
    GarminSyncRun,
)
from healthcurve.integrations.garmin.presentation import sync_origin_label
from healthcurve.labs.models import LabDocument, LabDocumentStatus
from healthcurve.operations import audit
from healthcurve.operations.audit import AuditEntry
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
    can_acknowledge: bool = False


GARMIN_WARNING_LABELS = {
    "intraday_heart_rate_missing_or_invalid": "intraday heart rate was missing or unusable",
    "intraday_respiration_rate_missing_or_invalid": "intraday respiration was missing or unusable",
    "intraday_stress_missing_or_invalid": "intraday stress was missing or unusable",
    "intraday_hrv_missing_or_invalid": "intraday HRV was missing or unusable",
    "hrv_nightly_average_shape_invalid": "nightly-average HRV used an unexpected response format",
}

# An open episode is valid and may span days. After one full day, however, it is
# useful to ask the owner whether it is genuinely continuing. This is a review
# threshold only: HealthCurve never infers or writes an end time.
OPEN_EPISODE_REVIEW_AFTER = timedelta(hours=24)


def _elapsed_label(duration: timedelta) -> str:
    total_minutes = max(0, int(duration.total_seconds() // 60))
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if not parts or minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts)


def _episode_start_label(episode: StressEpisode) -> str:
    local = episode.started_at.astimezone(ZoneInfo(episode.timezone))
    return f"{local.strftime('%b')} {local.day}, {local.year} at {local.strftime('%H:%M %Z')}"


def _garmin_warning_label(code: str) -> str:
    known = GARMIN_WARNING_LABELS.get(code)
    if known is not None:
        return known
    if code.startswith("intraday_") and code.endswith("_shape_invalid"):
        metric = code.removeprefix("intraday_").removesuffix("_shape_invalid")
        return f"intraday {metric.replace('_', ' ')} used an unexpected response format"
    if code.startswith("intraday_") and code.endswith("_truncated"):
        metric = code.removeprefix("intraday_").removesuffix("_truncated")
        return f"intraday {metric.replace('_', ' ')} may be incomplete"
    if code.startswith("intraday_") and code.endswith("_duplicate_timestamp"):
        metric = code.removeprefix("intraday_").removesuffix("_duplicate_timestamp")
        return f"intraday {metric.replace('_', ' ')} included duplicate timestamps"
    return code.replace("_", " ")


def _garmin_sync_acknowledged(
    session: Session, *, owner_id: uuid.UUID, sync_run_id: uuid.UUID
) -> bool:
    return (
        session.scalar(
            select(AuditEntry.id).where(
                AuditEntry.actor == audit.actor_for_owner(owner_id),
                AuditEntry.action == audit.AuditAction.DATA_QUALITY_ACKNOWLEDGED,
                AuditEntry.target_type == "garmin_sync_run",
                AuditEntry.target_id == sync_run_id,
            )
        )
        is not None
    )


def findings_for_owner(
    session: Session, owner_id: uuid.UUID, *, now: datetime | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    current_time = (now or datetime.now(UTC)).astimezone(UTC)
    review_cutoff = current_time - OPEN_EPISODE_REVIEW_AFTER
    old_open_episodes = session.scalars(
        select(StressEpisode).where(
            StressEpisode.owner_id == owner_id,
            StressEpisode.status == EpisodeStatus.OPEN,
            StressEpisode.ended_at.is_(None),
            StressEpisode.started_at <= review_cutoff,
        )
    )
    for episode in old_open_episodes:
        elapsed = current_time - episode.started_at.astimezone(UTC)
        findings.append(
            Finding(
                id=f"open-episode:{episode.id}",
                finding_kind="problem",
                severity="attention",
                source="Stress episode",
                title="Episode may still be open",
                detail=(
                    f"“{episode.trigger}” started {_episode_start_label(episode)} and has "
                    f"remained open for {_elapsed_label(elapsed)}. Confirm that it is still "
                    "continuing or record its actual end time; HealthCurve has not inferred an end."
                ),
                record_id=episode.id,
                href=(f"/episodes?history=all&review_episode={episode.id}#episode-{episode.id}"),
                action_label="Review or close episode",
            )
        )
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

    connection = session.scalar(
        select(GarminConnection).where(GarminConnection.owner_id == owner_id)
    )
    if connection is not None:
        if connection.state is GarminConnectionState.REAUTHENTICATION_REQUIRED:
            findings.append(
                Finding(
                    id=f"garmin-connection:{connection.id}:reauthentication",
                    finding_kind="problem",
                    severity="warning",
                    source="Garmin Connect",
                    title="Garmin sign-in needs attention",
                    detail=(
                        "Automatic sync is paused. Reconnect locally; no missing value "
                        "has been inferred as zero."
                    ),
                    record_id=connection.id,
                    href="/settings#garmin-connection",
                    action_label="Review Garmin connection",
                )
            )
        elif (
            connection.state is GarminConnectionState.CONNECTED
            and connection.last_success_at is None
        ):
            findings.append(
                Finding(
                    id=f"garmin-connection:{connection.id}:never-synced",
                    finding_kind="problem",
                    severity="attention",
                    source="Garmin Connect",
                    title="Garmin has not completed its first sync",
                    detail="The connection exists, but no successful automatic sync is recorded.",
                    record_id=connection.id,
                    href="/settings#garmin-connection",
                    action_label="Review Garmin connection",
                )
            )
        for metric, availability in sorted(connection.capabilities.items()):
            if availability != "unavailable":
                continue
            findings.append(
                Finding(
                    id=f"garmin-capability:{connection.id}:{metric}",
                    finding_kind="genuine_absence",
                    severity="attention",
                    source="Garmin Connect",
                    title=f"{metric.replace('_', ' ').title()} unavailable",
                    detail=(
                        "The latest automatic sync did not supply this metric; no zero "
                        "value was inferred."
                    ),
                    record_id=connection.id,
                    href="/settings#garmin-connection",
                    action_label="Review Garmin connection",
                )
            )
        latest_sync = session.scalar(
            select(GarminSyncRun)
            .where(GarminSyncRun.owner_id == owner_id)
            .order_by(GarminSyncRun.finished_at.desc(), GarminSyncRun.id.desc())
            .limit(1)
        )
        if (
            latest_sync is not None
            and latest_sync.warning_codes
            and not _garmin_sync_acknowledged(
                session, owner_id=owner_id, sync_run_id=latest_sync.id
            )
        ):
            warnings = sorted({_garmin_warning_label(code) for code in latest_sync.warning_codes})
            count = len(warnings)
            finished = latest_sync.finished_at.isoformat(timespec="minutes")
            window = (
                f"{latest_sync.requested_start_date.isoformat()} through "
                f"{latest_sync.requested_end_date.isoformat()} ({latest_sync.timezone})"
            )
            imported = latest_sync.counts.get("created", 0)
            corrected = latest_sync.counts.get("corrected", 0)
            unchanged = latest_sync.counts.get("unchanged", 0)
            warning_word = "warning" if count == 1 else "warnings"
            origin = sync_origin_label(latest_sync.origin)
            findings.append(
                Finding(
                    id=f"garmin-sync:{latest_sync.id}",
                    finding_kind="problem",
                    severity="attention",
                    source=f"Garmin Connect · {origin.lower()}",
                    title=f"Garmin sync completed with {count} data {warning_word}",
                    detail=(
                        f"Request origin: {origin}. Completed {finished} for {window}. "
                        "This is a completed sync notice, "
                        "not queued or running work. Other supplied Garmin data was saved "
                        f"({imported} new, {corrected} corrected, {unchanged} unchanged). "
                        f"Review: {'; '.join(warnings)}. Missing values remain missing, never zero."
                    ),
                    record_id=latest_sync.id,
                    href="/settings#garmin-connection",
                    action_label="Open Garmin sync settings",
                    can_acknowledge=True,
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
