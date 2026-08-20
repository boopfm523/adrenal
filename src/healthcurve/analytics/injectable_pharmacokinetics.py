"""Evidence-versioned mixed oral and 50 mg IV-push hydrocortisone scenario.

The oral contribution is exactly the existing wake-free v3 model. The intravenous
contribution is a separate total-serum cortisol exponential fitted to repeated
50 mg IV hydrocortisone boluses in primary adrenal insufficiency. The combined
total concentration is converted to serum-free cortisol with the same saturable
binding equation used by v3. This is an exploratory population model, not a
measurement, adequacy test, or dosing guide.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.analytics import wake_pharmacokinetics as oral_model
from healthcurve.analytics.exposure import ExposureDose
from healthcurve.analytics.exposure import exclusion_reason as oral_exclusion_reason
from healthcurve.analytics.wake_reference import BINDING_REVISION, free_from_total, total_from_free
from healthcurve.events import service as event_service
from healthcurve.medications.models import DoseCategory, DoseEvent, DoseUnit, Route

MODEL_ID: Final = "hc-mixed-route-free-v4"
MODEL_REVISION: Final = "hc-mixed-route-free-v4.0.0"
SERIES_KIND: Final = oral_model.SERIES_KIND
SERIES_NAME: Final = oral_model.SERIES_NAME
SERIES_UNIT: Final = oral_model.SERIES_UNIT
SAFETY_LABEL: Final = (
    "Population-parameter modeled serum-free-cortisol scenario from supported oral and "
    "50 mg IV-push facts—not a measured cortisol value, personal target, "
    "medication-adequacy test, or dosing guide."
)

# Prete et al. fitted repeated 50 mg IV boluses in primary adrenal insufficiency
# using Q * exp(-k*t). Q is the initial TOTAL serum cortisol increment for one
# 50 mg bolus and k is the fitted first-order elimination constant.
IV_PUSH_AMOUNT_MG: Final = Decimal("50")
IV_INITIAL_TOTAL_NMOL_L: Final = 1347.0
IV_ELIMINATION_RATE_PER_HOUR: Final = 0.27
IV_ELIMINATION_HALF_LIFE_HOURS: Final = math.log(2.0) / IV_ELIMINATION_RATE_PER_HOUR
IV_HORIZON_HOURS: Final = 24
HORIZON_HOURS: Final = max(oral_model.HORIZON_HOURS, IV_HORIZON_HOURS)
INJECTABLE_NAMES: Final = frozenset({"hydrocortisone inj dose", "hydrocortisone sodium succinate"})
INJECTABLE_FORMULATIONS: Final = frozenset(
    {"injection", "intravenous injection", "intravenous push", "iv push"}
)


def injectable_exclusion_reason(dose: ExposureDose) -> str | None:
    if dose.normalized_medication_name.strip().lower() not in INJECTABLE_NAMES:
        return "unsupported_medication"
    if (dose.formulation or "").strip().lower() not in INJECTABLE_FORMULATIONS:
        return "unsupported_formulation"
    if dose.route is not Route.INTRAVENOUS:
        return "unsupported_route"
    if dose.unit is not DoseUnit.MG:
        return "unsupported_unit"
    if dose.amount != IV_PUSH_AMOUNT_MG:
        return "unsupported_amount"
    return None


def exclusion_reason(dose: ExposureDose) -> str | None:
    if oral_exclusion_reason(dose) is None:
        return None
    return injectable_exclusion_reason(dose)


def _iv_total_contribution(dose: ExposureDose, instant: datetime) -> float:
    if injectable_exclusion_reason(dose) is not None:
        return 0.0
    elapsed = (instant.astimezone(UTC) - dose.occurred_at.astimezone(UTC)).total_seconds() / 3600
    if elapsed < 0.0 or elapsed > IV_HORIZON_HOURS:
        return 0.0
    return IV_INITIAL_TOTAL_NMOL_L * math.exp(-IV_ELIMINATION_RATE_PER_HOUR * elapsed)


def _sample_instants(
    *,
    start: datetime,
    end: datetime,
    supported_doses: list[ExposureDose],
    oral_peak_time_hours: float,
) -> list[datetime]:
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    instants = {start_utc, end_utc}
    cursor = start_utc
    step = timedelta(minutes=oral_model.SAMPLE_INTERVAL_MINUTES)
    while cursor < end_utc:
        instants.add(cursor)
        cursor += step
    for dose in supported_doses:
        administered = dose.occurred_at.astimezone(UTC)
        if start_utc <= administered <= end_utc:
            instants.add(administered)
        if oral_exclusion_reason(dose) is None:
            peak = administered + timedelta(hours=oral_peak_time_hours)
            if start_utc <= peak <= end_utc:
                instants.add(peak)
    return sorted(instants)


def _fingerprint(doses: list[ExposureDose], parameters: oral_model.WakeFreeParameters) -> str:
    tokens = [
        {
            "id": str(dose.id),
            "occurred_at": dose.occurred_at.astimezone(UTC).isoformat(),
            "recorded_at": dose.recorded_at.astimezone(UTC).isoformat()
            if dose.recorded_at is not None
            else None,
            "source_revision": dose.source_revision,
            "supersedes_id": str(dose.supersedes_id) if dose.supersedes_id else None,
            "amount": str(dose.amount),
            "unit": dose.unit.value,
            "route": dose.route.value,
            "category": dose.category.value,
            "normalized_medication_name": dose.normalized_medication_name,
            "formulation": dose.formulation,
            "confirmation_state": dose.confirmation_state,
        }
        for dose in doses
    ]
    body = json.dumps(
        {
            "model_revision": MODEL_REVISION,
            "oral_model_revision": oral_model.MODEL_REVISION,
            "binding_revision": BINDING_REVISION,
            "oral_parameters": oral_model.parameter_payload(parameters),
            "iv_initial_total_nmol_l": IV_INITIAL_TOTAL_NMOL_L,
            "iv_elimination_rate_per_hour": IV_ELIMINATION_RATE_PER_HOUR,
            "iv_supported_amount_mg": str(IV_PUSH_AMOUNT_MG),
            "doses": tokens,
        },
        default=str,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_curve(
    *,
    day: date,
    timezone: str,
    doses: list[ExposureDose],
    parameters: oral_model.WakeFreeParameters = oral_model.DEFAULT_PARAMETERS,
) -> dict[str, object]:
    """Build one local day from current supported oral and IV-push facts."""
    parameters.validate()
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    ordered = sorted(doses, key=lambda dose: (dose.occurred_at.astimezone(UTC), dose.id))
    supported = [dose for dose in ordered if exclusion_reason(dose) is None]

    samples: list[dict[str, object]] = []
    for instant in _sample_instants(
        start=start,
        end=end,
        supported_doses=supported,
        oral_peak_time_hours=parameters.peak_time_hours,
    ):
        oral_regular_free = math.fsum(
            oral_model.contribution_nmol_per_liter(dose, instant, parameters=parameters)
            for dose in supported
            if dose.category is not DoseCategory.STRESS
        )
        oral_stress_free = math.fsum(
            oral_model.contribution_nmol_per_liter(dose, instant, parameters=parameters)
            for dose in supported
            if dose.category is DoseCategory.STRESS
        )
        oral_free = oral_regular_free + oral_stress_free
        oral_total = total_from_free(oral_free)
        oral_regular_total = oral_total * oral_regular_free / oral_free if oral_free > 0.0 else 0.0
        oral_stress_total = oral_total - oral_regular_total
        iv_regular_total = math.fsum(
            _iv_total_contribution(dose, instant)
            for dose in supported
            if dose.category is not DoseCategory.STRESS
        )
        iv_stress_total = math.fsum(
            _iv_total_contribution(dose, instant)
            for dose in supported
            if dose.category is DoseCategory.STRESS
        )
        regular_total = oral_regular_total + iv_regular_total
        stress_total = oral_stress_total + iv_stress_total
        combined_total = regular_total + stress_total
        combined_free = free_from_total(combined_total)
        regular_free = (
            combined_free * regular_total / combined_total if combined_total > 0.0 else 0.0
        )
        regular_free = max(0.0, min(combined_free, regular_free))
        combined_free_display = oral_model._display_decimal(combined_free)
        regular_free_display = oral_model._display_decimal(regular_free)
        stress_free_display = combined_free_display - regular_free_display
        local = instant.astimezone(zone)
        offset = local.utcoffset() or timedelta()
        samples.append(
            {
                "occurred_at": instant,
                "local_time": local.replace(tzinfo=None),
                "utc_offset_minutes": int(offset.total_seconds() // 60),
                "modeled_free_cortisol_nmol_l": combined_free_display,
                "regular_modeled_free_cortisol_nmol_l": regular_free_display,
                "stress_modeled_free_cortisol_nmol_l": stress_free_display,
                "derived_total_cortisol_nmol_l_display": oral_model._display_decimal(
                    combined_total
                ),
            }
        )

    markers: list[dict[str, object]] = []
    for dose in ordered:
        reason = exclusion_reason(dose)
        is_oral = oral_exclusion_reason(dose) is None
        markers.append(
            {
                "dose_event_id": dose.id,
                "occurred_at": dose.occurred_at.astimezone(UTC),
                "local_time": dose.local_time,
                "timezone": dose.timezone,
                "utc_offset_minutes": dose.utc_offset_minutes,
                "medication_name": dose.medication_name,
                "formulation": dose.formulation,
                "amount": dose.amount,
                "unit": dose.unit,
                "route": dose.route,
                "category": dose.category,
                "source_type": dose.source_type,
                "confirmation_state": dose.confirmation_state,
                "supersedes_id": dose.supersedes_id,
                "supported": reason is None,
                "exclusion_reason": reason,
                "carryover": dose.occurred_at.astimezone(UTC) < start.astimezone(UTC),
                "modeled_peak_at": (
                    dose.occurred_at.astimezone(UTC)
                    + (timedelta(hours=parameters.peak_time_hours) if is_oral else timedelta())
                    if reason is None
                    else None
                ),
            }
        )

    oral_curve = oral_model.build_curve(
        day=day,
        timezone=timezone,
        doses=[],
        parameters=parameters,
    )
    model = dict(oral_curve["model"])
    model.update(
        {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "supported_medication": "hydrocortisone oral or Hydrocortisone Inj Dose",
            "supported_formulation": "immediate-release tablet or 50 mg IV push",
            "supported_route": Route.ORAL,
            "supported_medications": ["hydrocortisone", "Hydrocortisone Inj Dose"],
            "supported_formulations": ["conventional immediate-release tablet", "intravenous push"],
            "supported_routes": [Route.ORAL, Route.INTRAVENOUS],
            "iv_push_supported_amount_mg": IV_PUSH_AMOUNT_MG,
            "iv_push_initial_total_cortisol_nmol_l": Decimal(str(IV_INITIAL_TOTAL_NMOL_L)),
            "iv_push_elimination_rate_per_hour": Decimal(str(IV_ELIMINATION_RATE_PER_HOUR)),
            "iv_push_elimination_half_life_hours": Decimal(str(IV_ELIMINATION_HALF_LIFE_HOURS)),
            "references": [
                *model["references"],
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC7241266/",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC4280712/",
                "https://www.accessdata.fda.gov/drugsatfda_docs/label/2024/009866s121lbl.pdf",
                "https://pubmed.ncbi.nlm.nih.gov/7120045/",
            ],
        }
    )
    return {
        "date": day,
        "timezone": timezone,
        "day_start": start.astimezone(UTC),
        "day_end": end.astimezone(UTC),
        "elapsed_hours": Decimal(
            str((end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 3600)
        ),
        "series_kind": SERIES_KIND,
        "series_name": SERIES_NAME,
        "series_unit": SERIES_UNIT,
        "safety_label": SAFETY_LABEL,
        "definition": (
            "The unchanged wake-free v3 oral model plus a separately fitted 50 mg IV-push "
            "total-cortisol exponential. Total contributions are summed before nonlinear "
            "binding conversion to serum-free cortisol."
        ),
        "model": model,
        "source_revision_sha256": _fingerprint(ordered, parameters),
        "dose_markers": markers,
        "samples": samples,
        "supported_dose_count": len(supported),
        "excluded_dose_count": len(ordered) - len(supported),
    }


def curve_for_owner(
    session: Session,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
) -> dict[str, object]:
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    rows = list(
        session.scalars(
            select(DoseEvent)
            .where(
                DoseEvent.owner_id == owner_id,
                DoseEvent.occurred_at >= start.astimezone(UTC) - timedelta(hours=HORIZON_HOURS),
                DoseEvent.occurred_at < end,
            )
            .order_by(DoseEvent.occurred_at, DoseEvent.id)
        )
    )
    current = event_service.current_only(session, DoseEvent, rows)
    parameters = oral_model.active_parameters(session, owner_id=owner_id)
    doses = [
        ExposureDose(
            id=row.id,
            occurred_at=row.occurred_at,
            local_time=row.local_time,
            timezone=row.timezone,
            utc_offset_minutes=row.utc_offset_minutes,
            amount=row.amount,
            unit=row.unit,
            route=row.route,
            category=row.category,
            medication_name=row.medication.name,
            normalized_medication_name=row.medication.normalized_name,
            formulation=row.medication.formulation,
            source_type=row.source_type.value,
            confirmation_state=row.confirmation_state.value,
            supersedes_id=row.supersedes_id,
            recorded_at=row.recorded_at,
            source_revision=row.source_revision,
        )
        for row in current
    ]
    return build_curve(day=day, timezone=timezone, parameters=parameters, doses=doses)
