"""Authenticated deterministic analytics endpoints."""

from __future__ import annotations

import csv
import uuid
from datetime import UTC, date, datetime, time, timedelta
from io import StringIO
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from healthcurve.ai import analysis as analysis_service
from healthcurve.ai.models import AIAnalysis, AnalysisType
from healthcurve.analytics import (
    circadian_context,
    day_analysis,
    exposure,
    patterns,
    physiology,
    service,
)
from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.schemas import (
    AnalyticsSummaryOut,
    DailyPatternsOut,
    DayAnalysisGenerationOut,
    DayAnalysisOut,
    PatternAnalysisGenerationOut,
    PatternAnalysisOut,
    PhysiologicalCortisolCurveOut,
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


@router.get(
    "/analytics/steroid-exposure",
    response_model=SteroidExposureCurveOut | PhysiologicalCortisolCurveOut,
)
def steroid_exposure_curve(
    session: DbSession,
    owner: CurrentOwner,
    day: date,
    timezone: str | None = None,
    model: Literal["hc-exposure-v1", "hc-physiology-v2"] = "hc-exposure-v1",
):
    """Return the selected deterministic model from current owner-scoped dose facts."""
    zone_name = timezone or owner.default_timezone
    try:
        ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid timezone") from exc
    if model == "hc-exposure-v1":
        return exposure.curve_for_owner(session, owner_id=owner.id, day=day, timezone=zone_name)
    curve = physiology.curve_for_owner(session, owner_id=owner.id, day=day, timezone=zone_name)
    sample_instants = [
        cast(datetime, sample["occurred_at"])
        for sample in cast(list[dict[str, object]], curve["samples"])
    ]
    curve["context_band"] = circadian_context.build_band(
        day=day,
        timezone=zone_name,
        sample_instants=sample_instants,
    )
    return curve


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


def _day_analysis_out(row: AIAnalysis, *, stale: bool) -> DayAnalysisOut:
    inputs = row.computed_inputs
    if inputs is None:
        raise ValueError("day analysis provenance is incomplete")
    selected_date = inputs.get("selected_local_date")
    timezone = inputs.get("selected_timezone")
    revision = inputs.get("source_revision_sha256")
    if not isinstance(selected_date, str) or not isinstance(timezone, str):
        raise ValueError("day analysis selection provenance is incomplete")
    if not isinstance(revision, str) or len(revision) != 64:
        raise ValueError("day analysis source revision is incomplete")
    return DayAnalysisOut(
        id=row.id,
        analysis_type="daily_summary",
        body=row.body,
        source_record_count=len(row.source_record_ids),
        selected_date=date.fromisoformat(selected_date),
        timezone=timezone,
        source_revision_sha256=revision,
        stale=stale,
        generated_at=row.generated_at,
        model_name=row.model_name,
        model_digest=row.model_digest,
        prompt_version=row.prompt_version,
        schema_version=row.schema_version,
    )


def _safe_generation_detail(outcome: analysis_service.AnalysisOutcome) -> str | None:
    return {
        analysis_service.AnalysisOutcome.REFUSED: "The local model refused this request safely.",
        analysis_service.AnalysisOutcome.MODEL_UNAVAILABLE: (
            "HealthCurve could not reach the configured private model on its host. Confirm "
            "Ollama is running, then try again. Recorded facts and the HealthCurve remain "
            "available."
        ),
        analysis_service.AnalysisOutcome.MODEL_TIMEOUT: (
            "The configured private model did not finish within HealthCurve's time limit. "
            "It may still be loading; wait a moment and try again. Recorded facts and the "
            "HealthCurve remain available."
        ),
        analysis_service.AnalysisOutcome.INVALID: (
            "The generated analysis failed HealthCurve's citation or safety checks and was "
            "not saved."
        ),
    }.get(outcome)


def _latest_day_analysis(
    session: DbSession, *, owner_id: uuid.UUID, day: date, timezone: str
) -> AIAnalysis | None:
    row = session.scalar(
        select(AIAnalysis)
        .where(
            AIAnalysis.owner_id == owner_id,
            AIAnalysis.analysis_type == AnalysisType.DAILY_SUMMARY,
            AIAnalysis.hidden_at.is_(None),
            AIAnalysis.computed_inputs["selected_local_date"].astext == day.isoformat(),
            AIAnalysis.computed_inputs["selected_timezone"].astext == timezone,
        )
        .order_by(AIAnalysis.generated_at.desc(), AIAnalysis.id.desc())
        .limit(1)
    )
    return row if row is not None and analysis_service.is_renderable_analysis(row) else None


@router.get("/analytics/day-analysis", response_model=DayAnalysisOut | None)
def get_day_analysis(
    session: DbSession,
    owner: CurrentOwner,
    day: date,
    timezone: str | None = None,
) -> DayAnalysisOut | None:
    """Return the latest generated interpretation and whether its facts have changed."""
    zone_name = _validated_range(
        date_from=day,
        date_to=day,
        timezone=timezone,
        default_timezone=owner.default_timezone,
    )
    row = _latest_day_analysis(session, owner_id=owner.id, day=day, timezone=zone_name)
    if row is None:
        return None
    projection = day_analysis.build_projection(
        session, owner_id=owner.id, day=day, timezone=zone_name
    )
    return _day_analysis_out(
        row,
        stale=row.computed_inputs is None
        or row.computed_inputs.get("source_revision_sha256")
        != projection["source_revision_sha256"],
    )


@router.post(
    "/analytics/day-analysis",
    response_model=DayAnalysisGenerationOut,
    dependencies=[Depends(require_csrf)],
)
def generate_day_analysis(
    session: DbSession,
    owner: CurrentOwner,
    day: date,
    timezone: str | None = None,
) -> DayAnalysisGenerationOut:
    """Generate a checked private-model interpretation from the complete day projection."""
    zone_name = _validated_range(
        date_from=day,
        date_to=day,
        timezone=timezone,
        default_timezone=owner.default_timezone,
    )
    projection = day_analysis.build_projection(
        session, owner_id=owner.id, day=day, timezone=zone_name
    )
    retained_inputs = {
        name: projection[name]
        for name in (
            "projection_version",
            "selected_local_date",
            "selected_timezone",
            "data_availability_counts",
            "missing_domains",
            "source_revision_sha256",
        )
    }
    model_inputs = day_analysis.build_model_inputs(projection)
    retained_inputs["model_input_version"] = model_inputs["model_input_version"]
    citation_source = str(projection["source_record_id"])
    retained_source_ids = cast(list[str], projection["source_record_ids"])
    generated = analysis_service.generate_analysis(
        session,
        owner_id=owner.id,
        analysis_type=AnalysisType.DAILY_SUMMARY,
        source_record_ids=[citation_source],
        computed_inputs=model_inputs,
        system_prompt=analysis_service.DAY_SYSTEM_PROMPT,
        prompt_version=analysis_service.DAY_PROMPT_VERSION,
        persisted_source_record_ids=[
            citation_source,
            *retained_source_ids,
        ],
        persisted_inputs=retained_inputs,
        max_output_tokens=analysis_service.DAY_MAX_OUTPUT_TOKENS,
        context_window=analysis_service.DAY_CONTEXT_WINDOW,
        deterministic_safety_fields=True,
    )
    if generated.analysis is not None:
        zone = ZoneInfo(zone_name)
        generated.analysis.range_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(
            UTC
        )
        generated.analysis.range_end = datetime.combine(
            day + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(UTC)
        session.flush()
    return DayAnalysisGenerationOut(
        outcome=generated.outcome.value,
        detail=_safe_generation_detail(generated.outcome),
        analysis=(
            _day_analysis_out(generated.analysis, stale=False)
            if generated.analysis is not None
            else None
        ),
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
    computed_inputs: dict[str, object] = projection.longitudinal_summary.model_dump(mode="json")
    computed_inputs.update(
        {
            "selected_date_from": date_from.isoformat(),
            "selected_date_to": date_to.isoformat(),
            "selected_timezone": zone_name,
        }
    )
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
        analysis_service.AnalysisOutcome.MODEL_TIMEOUT: (
            "The configured private model did not finish within HealthCurve's time limit. "
            "Deterministic results remain available."
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
def list_pattern_analyses(
    session: DbSession,
    owner: CurrentOwner,
    date_from: date | None = None,
    date_to: date | None = None,
    timezone: str | None = None,
) -> list[PatternAnalysisOut]:
    """Return recent drafts, optionally narrowed to one exact local-date selection."""
    if (date_from is None) != (date_to is None):
        raise HTTPException(status_code=422, detail="date_from and date_to must be used together")
    statement = select(AIAnalysis).where(
        AIAnalysis.owner_id == owner.id,
        AIAnalysis.analysis_type == AnalysisType.PATTERN_OBSERVATION,
        AIAnalysis.hidden_at.is_(None),
    )
    limit = 20
    if date_from is not None and date_to is not None:
        zone_name = _validated_range(
            date_from=date_from,
            date_to=date_to,
            timezone=timezone,
            default_timezone=owner.default_timezone,
        )
        zone = ZoneInfo(zone_name)
        range_start = datetime.combine(date_from, time.min, tzinfo=zone).astimezone(UTC)
        range_end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone).astimezone(
            UTC
        )
        statement = statement.where(
            AIAnalysis.range_start == range_start,
            AIAnalysis.range_end == range_end,
            AIAnalysis.computed_inputs["selected_date_from"].astext == date_from.isoformat(),
            AIAnalysis.computed_inputs["selected_date_to"].astext == date_to.isoformat(),
            AIAnalysis.computed_inputs["selected_timezone"].astext == zone_name,
        )
        limit = 1
    rows = session.scalars(
        statement.order_by(AIAnalysis.generated_at.desc(), AIAnalysis.id.desc()).limit(limit)
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
                "gap_count",
                "largest_gap_minutes",
                "samples_without_cadence",
                "missingness_state",
                "summary_version",
                "source_revision_watermark_sha256",
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
                    f"{prefix}_gap_count": wearable.gap_count,
                    f"{prefix}_largest_gap_minutes": wearable.largest_gap_minutes,
                    f"{prefix}_samples_without_cadence": wearable.samples_without_cadence,
                    f"{prefix}_missingness_state": wearable.missingness_state,
                    f"{prefix}_summary_version": wearable.summary_version,
                    f"{prefix}_source_revision_watermark_sha256": (
                        wearable.source_revision_watermark_sha256
                    ),
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
