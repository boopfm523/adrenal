"""Authenticated deterministic analytics endpoints."""

from __future__ import annotations

import csv
import uuid
from datetime import UTC, date, datetime, time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from healthcurve.ai import analysis as analysis_service
from healthcurve.ai.models import AIAnalysis, AnalysisType
from healthcurve.analytics import exposure, patterns, service
from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.schemas import (
    AnalyticsSummaryOut,
    DailyPatternsOut,
    PatternAnalysisGenerationOut,
    PatternAnalysisOut,
    SteroidExposureCurveOut,
)
from healthcurve.operations import audit

router = APIRouter(tags=["analytics"])
MAX_RANGE_DAYS = 366


def _validated_range(
    *, date_from: date, date_to: date, timezone: str | None, default_timezone: str
) -> str:
    zone_name = timezone or default_timezone
    try:
        ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid timezone") from exc
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=f"range cannot exceed {MAX_RANGE_DAYS} days")
    return zone_name


@router.get("/analytics/steroid-exposure", response_model=SteroidExposureCurveOut)
def steroid_exposure_curve(
    session: DbSession,
    owner: CurrentOwner,
    day: date,
    timezone: str | None = None,
):
    """Return a theoretical relative exposure curve from current recorded doses."""
    zone_name = timezone or owner.default_timezone
    try:
        ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid timezone") from exc
    return exposure.curve_for_owner(session, owner_id=owner.id, day=day, timezone=zone_name)


@router.get("/analytics/summary", response_model=AnalyticsSummaryOut)
def analytics_summary(
    session: DbSession,
    owner: CurrentOwner,
    date_from: date,
    date_to: date,
    timezone: str | None = None,
):
    zone_name = _validated_range(
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        default_timezone=owner.default_timezone,
    )
    return service.summary_for_owner(
        session,
        owner_id=owner.id,
        date_from=date_from,
        date_to=date_to,
        timezone=zone_name,
    )


@router.get("/analytics/daily-patterns", response_model=DailyPatternsOut)
def daily_patterns(
    session: DbSession,
    owner: CurrentOwner,
    date_from: date,
    date_to: date,
    timezone: str | None = None,
):
    """Return comparable deterministic features for at most 366 local days."""
    zone_name = _validated_range(
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        default_timezone=owner.default_timezone,
    )
    return patterns.daily_patterns_for_owner(
        session,
        owner_id=owner.id,
        date_from=date_from,
        date_to=date_to,
        timezone=zone_name,
    )


def _pattern_analysis_out(row: AIAnalysis) -> PatternAnalysisOut:
    if row.range_start is None or row.range_end is None or row.computed_inputs is None:
        raise ValueError("pattern analysis provenance is incomplete")
    return PatternAnalysisOut(
        id=row.id,
        analysis_type="pattern_observation",
        body=row.body,
        source_record_ids=row.source_record_ids,
        computed_inputs=row.computed_inputs,
        range_start=row.range_start,
        range_end=row.range_end,
        generated_at=row.generated_at,
        model_name=row.model_name,
        model_digest=row.model_digest,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
    )


@router.post(
    "/analytics/pattern-analysis",
    response_model=PatternAnalysisGenerationOut,
    dependencies=[Depends(require_csrf)],
)
def generate_pattern_analysis(
    session: DbSession,
    owner: CurrentOwner,
    date_from: date,
    date_to: date,
    timezone: str | None = None,
) -> PatternAnalysisGenerationOut:
    """Optionally phrase the deterministic range summary through the private model."""
    zone_name = _validated_range(
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        default_timezone=owner.default_timezone,
    )
    projection = DailyPatternsOut.model_validate(
        patterns.daily_patterns_for_owner(
            session,
            owner_id=owner.id,
            date_from=date_from,
            date_to=date_to,
            timezone=zone_name,
        )
    )
    source_ids = [
        f"daily-feature:{day.date}:{day.source_revision_watermark_sha256}"
        for day in projection.days
    ]
    computed_inputs = projection.longitudinal_summary.model_dump(mode="json")
    generated = analysis_service.generate_analysis(
        session,
        owner_id=owner.id,
        analysis_type=AnalysisType.PATTERN_OBSERVATION,
        source_record_ids=source_ids,
        computed_inputs=computed_inputs,
    )
    if generated.analysis is not None:
        zone = ZoneInfo(zone_name)
        generated.analysis.range_start = datetime.combine(
            date_from, time.min, tzinfo=zone
        ).astimezone(UTC)
        generated.analysis.range_end = datetime.combine(
            date_to + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(UTC)
        session.flush()
    safe_detail = {
        analysis_service.AnalysisOutcome.REFUSED: "The local model refused this request safely.",
        analysis_service.AnalysisOutcome.MODEL_UNAVAILABLE: (
            "The configured private model is unavailable. Deterministic results remain available."
        ),
        analysis_service.AnalysisOutcome.INVALID: (
            "The generated draft failed HealthCurve's citation or safety checks and was not saved."
        ),
    }.get(generated.outcome)
    return PatternAnalysisGenerationOut.model_validate(
        {
            "outcome": generated.outcome.value,
            "detail": safe_detail,
            "analysis": (
                _pattern_analysis_out(generated.analysis)
                if generated.analysis is not None
                else None
            ),
        }
    )


@router.get("/analytics/pattern-analysis", response_model=list[PatternAnalysisOut])
def list_pattern_analyses(session: DbSession, owner: CurrentOwner) -> list[PatternAnalysisOut]:
    rows = session.scalars(
        select(AIAnalysis)
        .where(
            AIAnalysis.owner_id == owner.id,
            AIAnalysis.analysis_type == AnalysisType.PATTERN_OBSERVATION,
            AIAnalysis.hidden_at.is_(None),
        )
        .order_by(AIAnalysis.generated_at.desc(), AIAnalysis.id.desc())
        .limit(20)
    )
    return [
        _pattern_analysis_out(row)
        for row in rows
        if analysis_service.is_renderable_analysis(row)
        and row.range_start is not None
        and row.range_end is not None
    ]


@router.delete(
    "/analytics/pattern-analysis/{analysis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_pattern_analysis(
    analysis_id: uuid.UUID, session: DbSession, owner: CurrentOwner
) -> Response:
    row = session.scalar(
        select(AIAnalysis).where(
            AIAnalysis.id == analysis_id,
            AIAnalysis.owner_id == owner.id,
            AIAnalysis.analysis_type == AnalysisType.PATTERN_OBSERVATION,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="pattern analysis not found")
    session.delete(row)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.AI_ANALYSIS_DELETED,
        target_type="ai_analysis",
        target_id=row.id,
        change_summary="type=pattern_observation",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/analytics/daily-patterns.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def daily_patterns_csv(
    session: DbSession,
    owner: CurrentOwner,
    date_from: date,
    date_to: date,
    timezone: str | None = None,
) -> Response:
    """Export the same current daily-feature projection as a flat CSV."""
    zone_name = _validated_range(
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        default_timezone=owner.default_timezone,
    )
    result = DailyPatternsOut.model_validate(
        patterns.daily_patterns_for_owner(
            session,
            owner_id=owner.id,
            date_from=date_from,
            date_to=date_to,
            timezone=zone_name,
        )
    )
    stream = StringIO(newline="")
    fieldnames = [
        "date",
        "timezone",
        "elapsed_hours",
        "feature_version",
        "exposure_model_version",
        "dose_plan_version_ids",
        "source_revision_watermark_sha256",
        "supported_dose_count",
        "excluded_dose_count",
        "exposure_peak_reu",
        "exposure_peak_at",
        "exposure_auc_reu_hours",
        "symptom_count",
        "symptom_severity_sample_count",
        "symptom_severity_missing_count",
        "average_symptom_severity",
        "stress_episode_count",
        "stress_episode_overlap_minutes",
        "blood_pressure_sample_count",
    ]
    for metric in patterns.METRICS:
        fieldnames.extend(
            f"{metric.value}_{suffix}"
            for suffix in (
                "sample_count",
                "unit",
                "minimum",
                "average",
                "maximum",
                "observed_coverage_percent",
                "samples_without_cadence",
                "missingness_state",
            )
        )
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    for day in result.days:
        row: dict[str, object] = {
            "date": day.date.isoformat(),
            "timezone": day.timezone,
            "elapsed_hours": day.elapsed_hours,
            "feature_version": day.feature_version,
            "exposure_model_version": day.exposure_model_version,
            "dose_plan_version_ids": ";".join(map(str, day.dose_plan_version_ids)),
            "source_revision_watermark_sha256": day.source_revision_watermark_sha256,
            "supported_dose_count": day.supported_dose_count,
            "excluded_dose_count": day.excluded_dose_count,
            "exposure_peak_reu": day.exposure_peak_reu,
            "exposure_peak_at": day.exposure_peak_at.isoformat(),
            "exposure_auc_reu_hours": day.exposure_auc_reu_hours,
            "symptom_count": day.symptom_count,
            "symptom_severity_sample_count": day.symptom_severity_sample_count,
            "symptom_severity_missing_count": day.symptom_severity_missing_count,
            "average_symptom_severity": day.average_symptom_severity,
            "stress_episode_count": day.stress_episodes.count,
            "stress_episode_overlap_minutes": day.stress_episodes.overlap_minutes,
            "blood_pressure_sample_count": day.blood_pressure.sample_count,
        }
        for wearable in day.wearables:
            prefix = wearable.metric_type.value
            row.update(
                {
                    f"{prefix}_sample_count": wearable.sample_count,
                    f"{prefix}_unit": wearable.unit,
                    f"{prefix}_minimum": wearable.minimum,
                    f"{prefix}_average": wearable.average,
                    f"{prefix}_maximum": wearable.maximum,
                    f"{prefix}_observed_coverage_percent": wearable.observed_coverage_percent,
                    f"{prefix}_samples_without_cadence": wearable.samples_without_cadence,
                    f"{prefix}_missingness_state": wearable.missingness_state,
                }
            )
        writer.writerow(row)
    return Response(
        stream.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="healthcurve-daily-patterns-{date_from}-{date_to}.csv"'
            ),
            "Cache-Control": "no-store",
        },
    )
