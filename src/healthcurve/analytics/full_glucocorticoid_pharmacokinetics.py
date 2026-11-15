"""Evidence-versioned combined hydrocortisone + prednisone glucocorticoid scenario.

v5 keeps the unchanged ``hc-mixed-route-free-v4`` hydrocortisone calculation (the
oral wake-free v3 model plus 50/100 mg IV-push facts) and adds a second,
independently sourced contribution for oral prednisone. Prednisone is
pharmacologically distinct from cortisol -- it is converted to active prednisolone
and binds the glucocorticoid receptor with materially different affinity and
timing -- so this model never treats a milligram of prednisone as a milligram of
hydrocortisone. Instead it:

1. shapes the prednisone contribution using prednisolone's own published
   absorption and elimination timing (not hydrocortisone's), and
2. scales that shape's amplitude using a long-standing clinical glucocorticoid
   dose-equivalence (5 mg prednisone ~= 20 mg hydrocortisone) applied to the same
   owner-reviewed hydrocortisone mg-to-nmol/L calibration v3/v4 already use, so
   both drugs land on one shared display axis.

The combined number is therefore a modeled **free-cortisol-equivalent** scenario,
not a literal prednisolone or cortisol assay value. Every payload keeps the
hydrocortisone and prednisone contributions separately so the owner can see which
drug produced how much of the modeled total. ADR-0039 defines this model, its
evidence, and its limits.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Final, cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.analytics import injectable_pharmacokinetics as hc_model
from healthcurve.analytics import wake_pharmacokinetics as oral_model
from healthcurve.analytics.exposure import ExposureDose
from healthcurve.analytics.wake_reference import BINDING_REVISION, free_from_total, total_from_free
from healthcurve.events import service as event_service
from healthcurve.medications.models import DoseCategory, DoseEvent, DoseUnit, Route

MODEL_ID: Final = "hc-full-glucocorticoid-v5"
MODEL_REVISION: Final = "hc-full-glucocorticoid-v5.0.0"
SERIES_KIND: Final = "modeled_free_glucocorticoid_equivalent_scenario"
SERIES_NAME: Final = "Modeled free-cortisol-equivalent scenario (hydrocortisone + prednisone)"
SERIES_UNIT: Final = "nmol/L"
SAFETY_LABEL: Final = (
    "Population-parameter modeled free-cortisol-equivalent scenario from supported "
    "hydrocortisone and prednisone facts—not a measured cortisol or prednisolone "
    "value, personal target, medication-adequacy test, or dosing guide. The prednisone "
    "portion uses a clinical glucocorticoid dose-equivalence scaling a prednisolone-"
    "specific absorption/elimination shape; it is not a direct assay of either drug."
)

# Prednisone is rapidly and almost completely converted to active prednisolone. Published
# single/multi-dose studies place oral peak plasma concentration at roughly 1-2 hours and
# the plasma elimination half-life at roughly 2-4 hours, with the longer, more heavily
# powered (n=43, 5-200 mg) nonlinear-bioavailability study anchoring half-life at the
# upper end of that range. v5 uses the midpoint of the commonly cited peak-time range and
# that study's half-life as transparent, literature-anchored approximations -- not a
# fitted owner-specific absorption constant.
PREDNISOLONE_PEAK_TIME_HOURS: Final = 1.5
PREDNISOLONE_ELIMINATION_HALF_LIFE_HOURS: Final = 3.5
PREDNISOLONE_ELIMINATION_RATE_PER_HOUR: Final = (
    math.log(2.0) / PREDNISOLONE_ELIMINATION_HALF_LIFE_HOURS
)
PREDNISOLONE_ABSORPTION_RATE_PER_HOUR: Final = oral_model.absorption_rate_for_timing(
    PREDNISOLONE_ELIMINATION_HALF_LIFE_HOURS, PREDNISOLONE_PEAK_TIME_HOURS
)
# Contribution is negligible but never exactly zero after a one-compartment decay; at this
# horizon the normalized shape is about 0.12% of its peak, matching the "small disclosed
# residual" convention the other versioned models already use.
PREDNISONE_HORIZON_HOURS: Final = 36

# A long-standing clinical glucocorticoid dose-equivalence table (oral/IV; mineralocorticoid
# activity and intramuscular/intra-articular potency are explicitly out of scope): cortisone
# 25 mg = hydrocortisone 20 mg = prednisone/prednisolone 5 mg = methylprednisolone 4 mg =
# dexamethasone 0.75 mg. This is a therapeutic/anti-inflammatory potency ratio, not a
# pharmacokinetic conversion; v5 uses it only to scale amplitude, never timing, and the
# prednisolone-specific shape above still governs when the contribution rises and falls.
PREDNISONE_HYDROCORTISONE_EQUIVALENCE_MG_PER_MG: Final = 4.0

PREDNISONE_NORMALIZED_NAMES: Final = frozenset({"prednisone"})
PREDNISONE_SUPPORTED_FORMULATIONS: Final = frozenset(
    {
        "tablet",
        "immediate release tablet",
        "immediate-release tablet",
        "conventional immediate-release tablet",
    }
)

HORIZON_HOURS: Final = max(hc_model.HORIZON_HOURS, PREDNISONE_HORIZON_HOURS)


def _normalized_formulation(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.casefold().strip().split())


def prednisone_exclusion_reason(dose: ExposureDose) -> str | None:
    """Return a stable reason code instead of guessing parameters (ADR-0013 style)."""
    if dose.normalized_medication_name.casefold().strip() not in PREDNISONE_NORMALIZED_NAMES:
        return "unsupported_medication"
    if _normalized_formulation(dose.formulation) not in PREDNISONE_SUPPORTED_FORMULATIONS:
        return "unsupported_formulation"
    if dose.route != Route.ORAL:
        return "unsupported_route"
    if dose.unit != DoseUnit.MG:
        return "unsupported_unit"
    return None


def exclusion_reason(dose: ExposureDose) -> str | None:
    """A dose is supported here if the unchanged v4 model supports it, or prednisone does."""
    if hc_model.exclusion_reason(dose) is None:
        return None
    return prednisone_exclusion_reason(dose)


def _prednisolone_normalized_shape(elapsed_hours: float) -> float:
    """Evaluate prednisolone's own unit-peak one-compartment absorption/elimination shape."""
    if elapsed_hours < 0.0 or elapsed_hours > PREDNISONE_HORIZON_HOURS:
        return 0.0
    ka = PREDNISOLONE_ABSORPTION_RATE_PER_HOUR
    ke = PREDNISOLONE_ELIMINATION_RATE_PER_HOUR
    peak = PREDNISOLONE_PEAK_TIME_HOURS
    raw = math.exp(-ke * elapsed_hours) - math.exp(-ka * elapsed_hours)
    raw_peak = math.exp(-ke * peak) - math.exp(-ka * peak)
    result = raw / raw_peak
    if not math.isfinite(result) or result < 0.0:
        raise ValueError("invalid prednisone PK result")
    return result


def prednisone_contribution_nmol_per_liter(
    dose: ExposureDose,
    instant: datetime,
    *,
    parameters: oral_model.WakeFreeParameters = oral_model.DEFAULT_PARAMETERS,
) -> float:
    """Return one prednisone dose's free-cortisol-equivalent contribution.

    The shape is prednisolone's own timing; the amplitude reuses the owner-reviewed
    hydrocortisone mg-to-nmol/L calibration at the clinically equivalent hydrocortisone
    dose, so a change to that calibration rescales both drugs consistently.
    """
    if prednisone_exclusion_reason(dose) is not None:
        return 0.0
    elapsed = (instant.astimezone(UTC) - dose.occurred_at.astimezone(UTC)).total_seconds() / 3600
    equivalent_hydrocortisone_mg = (
        float(dose.amount) * PREDNISONE_HYDROCORTISONE_EQUIVALENCE_MG_PER_MG
    )
    return (
        parameters.free_peak_10_mg_nmol_l
        * (equivalent_hydrocortisone_mg / 10.0)
        * _prednisolone_normalized_shape(elapsed)
    )


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
        if hc_model.oral_exclusion_reason(dose) is None:
            peak = administered + timedelta(hours=oral_peak_time_hours)
            if start_utc <= peak <= end_utc:
                instants.add(peak)
        if prednisone_exclusion_reason(dose) is None:
            peak = administered + timedelta(hours=PREDNISOLONE_PEAK_TIME_HOURS)
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
            "hc_model_revision": hc_model.MODEL_REVISION,
            "oral_model_revision": oral_model.MODEL_REVISION,
            "binding_revision": BINDING_REVISION,
            "oral_parameters": oral_model.parameter_payload(parameters),
            "prednisolone_peak_time_hours": PREDNISOLONE_PEAK_TIME_HOURS,
            "prednisolone_elimination_half_life_hours": PREDNISOLONE_ELIMINATION_HALF_LIFE_HOURS,
            "prednisone_hydrocortisone_equivalence_mg_per_mg": (
                PREDNISONE_HYDROCORTISONE_EQUIVALENCE_MG_PER_MG
            ),
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
    """Build one local day from current supported hydrocortisone and prednisone facts."""
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
            if hc_model.oral_exclusion_reason(dose) is None
            and dose.category is not DoseCategory.STRESS
        )
        oral_stress_free = math.fsum(
            oral_model.contribution_nmol_per_liter(dose, instant, parameters=parameters)
            for dose in supported
            if hc_model.oral_exclusion_reason(dose) is None and dose.category is DoseCategory.STRESS
        )
        hc_oral_free = oral_regular_free + oral_stress_free
        hc_oral_total = total_from_free(hc_oral_free)
        hc_oral_regular_total = (
            hc_oral_total * oral_regular_free / hc_oral_free if hc_oral_free > 0.0 else 0.0
        )
        hc_oral_stress_total = hc_oral_total - hc_oral_regular_total

        iv_regular_total = math.fsum(
            hc_model.iv_total_contribution(dose, instant)
            for dose in supported
            if dose.category is not DoseCategory.STRESS
        )
        iv_stress_total = math.fsum(
            hc_model.iv_total_contribution(dose, instant)
            for dose in supported
            if dose.category is DoseCategory.STRESS
        )

        hc_regular_total = hc_oral_regular_total + iv_regular_total
        hc_stress_total = hc_oral_stress_total + iv_stress_total
        hc_total = hc_regular_total + hc_stress_total

        prednisone_total = total_from_free(
            math.fsum(
                prednisone_contribution_nmol_per_liter(dose, instant, parameters=parameters)
                for dose in supported
                if prednisone_exclusion_reason(dose) is None
            )
        )

        combined_total = hc_total + prednisone_total
        combined_free = free_from_total(combined_total)

        hc_free = combined_free * hc_total / combined_total if combined_total > 0.0 else 0.0
        hc_free = max(0.0, min(combined_free, hc_free))
        hc_regular_free = hc_free * hc_regular_total / hc_total if hc_total > 0.0 else 0.0
        hc_regular_free = max(0.0, min(hc_free, hc_regular_free))

        combined_free_display = oral_model.display_decimal(combined_free)
        hc_free_display = oral_model.display_decimal(hc_free)
        prednisone_free_display = combined_free_display - hc_free_display
        hc_regular_free_display = oral_model.display_decimal(hc_regular_free)
        hc_stress_free_display = hc_free_display - hc_regular_free_display

        local = instant.astimezone(zone)
        offset = local.utcoffset() or timedelta()
        samples.append(
            {
                "occurred_at": instant,
                "local_time": local.replace(tzinfo=None),
                "utc_offset_minutes": int(offset.total_seconds() // 60),
                "modeled_free_cortisol_nmol_l": combined_free_display,
                "regular_modeled_free_cortisol_nmol_l": hc_regular_free_display,
                "stress_modeled_free_cortisol_nmol_l": hc_stress_free_display,
                "hydrocortisone_modeled_free_cortisol_nmol_l": hc_free_display,
                "prednisone_modeled_free_cortisol_equivalent_nmol_l": prednisone_free_display,
                "derived_total_cortisol_nmol_l_display": oral_model.display_decimal(combined_total),
            }
        )

    markers: list[dict[str, object]] = []
    for dose in ordered:
        reason = exclusion_reason(dose)
        is_oral_hc = hc_model.oral_exclusion_reason(dose) is None
        is_prednisone = prednisone_exclusion_reason(dose) is None
        if is_oral_hc:
            peak_delay = timedelta(hours=parameters.peak_time_hours)
        elif is_prednisone:
            peak_delay = timedelta(hours=PREDNISOLONE_PEAK_TIME_HOURS)
        else:
            peak_delay = timedelta()
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
                    dose.occurred_at.astimezone(UTC) + peak_delay if reason is None else None
                ),
            }
        )

    hc_curve = hc_model.build_curve(day=day, timezone=timezone, doses=[], parameters=parameters)
    model = dict(cast(dict[str, object], hc_curve["model"]))
    hc_references = cast(list[str], model.get("references", []))
    model.update(
        {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "supported_medication": (
                "hydrocortisone oral, Hydrocortisone Inj Dose, or prednisone oral"
            ),
            "supported_formulation": (
                "immediate-release tablet, 50/100 mg IV push, or immediate-release "
                "prednisone tablet"
            ),
            "supported_route": Route.ORAL,
            "supported_medications": ["hydrocortisone", "Hydrocortisone Inj Dose", "prednisone"],
            "supported_formulations": [
                "conventional immediate-release tablet",
                "intravenous push",
            ],
            "supported_routes": [Route.ORAL, Route.INTRAVENOUS],
            "prednisone_hydrocortisone_equivalence_mg_per_mg": Decimal(
                str(PREDNISONE_HYDROCORTISONE_EQUIVALENCE_MG_PER_MG)
            ),
            "prednisolone_absorption_rate_per_hour": Decimal(
                str(PREDNISOLONE_ABSORPTION_RATE_PER_HOUR)
            ),
            "prednisolone_elimination_rate_per_hour": Decimal(
                str(PREDNISOLONE_ELIMINATION_RATE_PER_HOUR)
            ),
            "prednisolone_elimination_half_life_hours": Decimal(
                str(PREDNISOLONE_ELIMINATION_HALF_LIFE_HOURS)
            ),
            "prednisolone_peak_time_hours": Decimal(str(PREDNISOLONE_PEAK_TIME_HOURS)),
            "prednisone_contribution_horizon_hours": PREDNISONE_HORIZON_HOURS,
            "references": [
                *hc_references,
                "https://go.drugbank.com/drugs/DB00635",
                "https://katalog.lib.cas.cz/KNAV/EdsRecord/edsair,edsair.doi.dedup.....4cce0d4713e4ed67d99ae25538910bae",
                "https://cir.nii.ac.jp/crid/1363951793207555456",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC10080509/table/Tab3",
                "https://litfl.com/steroid-conversion/",
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
            "The unchanged hc-mixed-route-free-v4 hydrocortisone calculation (oral plus "
            "50/100 mg IV-push) plus a separately shaped prednisone contribution using "
            "prednisolone's own published absorption/elimination timing, scaled in "
            "amplitude by a disclosed clinical hydrocortisone dose-equivalence. Total "
            "contributions are summed before nonlinear binding conversion to serum-free "
            "cortisol-equivalent, and the hydrocortisone/prednisone split is retained on "
            "every sample."
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
