"""Selectable wake-era free-cortisol pharmacokinetic scenario.

The equation is a deterministic population-assumption visualization based only on
recorded immediate-release oral hydrocortisone dose facts. It is not a cortisol
measurement, personal target, medication-adequacy test, or dosing guide.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.analytics.exposure import ExposureDose, exclusion_reason
from healthcurve.analytics.models import CortisolPkParameterRevision
from healthcurve.analytics.wake_reference import BINDING_REVISION, total_from_free
from healthcurve.events import service as event_service
from healthcurve.identity.models import Owner
from healthcurve.medications.models import DoseCategory, DoseEvent, DoseUnit, Route

MODEL_ID: Final = "hc-wake-free-v3"
MODEL_REVISION: Final = "hc-wake-free-v3.0.0"
PARAMETER_SOURCE_REVISION: Final = "owner-reviewed-hc-pk-reference-v1"
CALIBRATION_REVISION: Final = "10mg-1.7x-default-healthy-free-acrophase-v1"
SERIES_KIND: Final = "modeled_serum_free_cortisol_scenario"
SERIES_NAME: Final = "Modeled serum-free-cortisol scenario"
SERIES_UNIT: Final = "nmol/L"
SAFETY_LABEL: Final = (
    "Population-parameter modeled serum-free-cortisol scenario—not a measured cortisol "
    "value, personal target, medication-adequacy test, or dosing guide."
)
SAMPLE_INTERVAL_MINUTES: Final = 5
HORIZON_HOURS: Final = 72
DISPLAY_QUANTUM: Final = Decimal("0.000000001")
ZERO_RENDER_THRESHOLD: Final = Decimal("0.000000001")

# Reviewed owner-supplied pharmacokinetic reference defaults. Absorption duration and clearance
# are retained as source metadata; v3 uses tmax and half-life as its independent timing
# inputs because those population estimates came from different studies and are not
# algebraically identical to Vd/CL.
REFERENCE_ABSORPTION_DURATION_HOURS: Final = 0.54
REFERENCE_CLEARANCE_LITERS_PER_HOUR: Final = 12.1
DEFAULT_HEALTHY_FREE_ACROPHASE_NMOL_L: Final = 40.940736982
DEFAULT_FREE_PEAK_10_MG_NMOL_L: Final = 1.7 * DEFAULT_HEALTHY_FREE_ACROPHASE_NMOL_L


@dataclass(frozen=True, slots=True)
class WakeFreeParameters:
    elimination_half_life_hours: float = 1.6
    peak_time_hours: float = 1.1
    distribution_volume_liters: float = 38.7
    oral_bioavailability: float = 0.95
    revision_id: uuid.UUID | None = None
    revision_number: int = 0
    created_at: datetime | None = None
    source_revision: str = PARAMETER_SOURCE_REVISION

    def validate(self) -> None:
        bounded = (
            (self.elimination_half_life_hours, 0.25, 12.0),
            (self.peak_time_hours, 0.1, 8.0),
            (self.distribution_volume_liters, 1.0, 500.0),
            (self.oral_bioavailability, 0.000001, 1.0),
        )
        if not all(
            math.isfinite(value) and lower <= value <= upper for value, lower, upper in bounded
        ):
            raise ValueError("invalid wake-free PK parameter set")
        if self.revision_number < 0:
            raise ValueError("parameter revision cannot be negative")

    @property
    def elimination_rate_per_hour(self) -> float:
        self.validate()
        return math.log(2.0) / self.elimination_half_life_hours

    @property
    def absorption_rate_per_hour(self) -> float:
        """Solve the Bateman tmax relationship for a positive absorption rate."""
        self.validate()
        return _absorption_rate_for_timing(
            self.elimination_half_life_hours,
            self.peak_time_hours,
        )

    @property
    def derived_clearance_liters_per_hour(self) -> float:
        return self.elimination_rate_per_hour * self.distribution_volume_liters

    @property
    def free_peak_10_mg_nmol_l(self) -> float:
        return (
            DEFAULT_FREE_PEAK_10_MG_NMOL_L
            * (self.oral_bioavailability / DEFAULT_PARAMETERS.oral_bioavailability)
            * (DEFAULT_PARAMETERS.distribution_volume_liters / self.distribution_volume_liters)
        )


DEFAULT_PARAMETERS: Final = WakeFreeParameters()


@lru_cache(maxsize=128)
def _absorption_rate_for_timing(
    elimination_half_life_hours: float,
    peak_time_hours: float,
) -> float:
    """Return a bounded, reusable solution for immutable PK timing inputs.

    This cache contains only pure model constants. It never contains owner IDs,
    recorded facts, chart results, or database state, so a late or corrected fact
    still rebuilds its curve immediately.
    """
    ke = math.log(2.0) / elimination_half_life_hours

    def peak_time(ka: float) -> float:
        if abs(ka - ke) <= 1e-10:
            return 1.0 / ke
        return math.log(ka / ke) / (ka - ke)

    lower = 1e-8
    upper = max(1000.0, ke * 1000.0)
    for _ in range(160):
        midpoint = math.sqrt(lower * upper)
        if peak_time(midpoint) > peak_time_hours:
            lower = midpoint
        else:
            upper = midpoint
    result = math.sqrt(lower * upper)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError("tmax does not yield a valid absorption rate")
    return result


def parameters_from_revision(row: CortisolPkParameterRevision) -> WakeFreeParameters:
    return WakeFreeParameters(
        elimination_half_life_hours=float(row.elimination_half_life_hours),
        peak_time_hours=float(row.peak_time_hours),
        distribution_volume_liters=float(row.distribution_volume_liters),
        oral_bioavailability=float(row.oral_bioavailability),
        revision_id=row.id,
        revision_number=row.revision_number,
        created_at=row.created_at,
        source_revision=row.source_revision,
    )


def active_parameters(session: Session, *, owner_id: uuid.UUID) -> WakeFreeParameters:
    row = session.scalar(
        select(CortisolPkParameterRevision)
        .where(CortisolPkParameterRevision.owner_id == owner_id)
        .order_by(CortisolPkParameterRevision.revision_number.desc())
        .limit(1)
    )
    return DEFAULT_PARAMETERS if row is None else parameters_from_revision(row)


def create_parameter_revision(
    session: Session,
    *,
    owner_id: uuid.UUID,
    elimination_half_life_hours: Decimal,
    peak_time_hours: Decimal,
    distribution_volume_liters: Decimal,
    oral_bioavailability: Decimal,
) -> CortisolPkParameterRevision:
    """Create an immutable replacement revision under an owner-row lock."""
    session.execute(select(Owner.id).where(Owner.id == owner_id).with_for_update()).scalar_one()
    previous = session.scalar(
        select(CortisolPkParameterRevision)
        .where(CortisolPkParameterRevision.owner_id == owner_id)
        .order_by(CortisolPkParameterRevision.revision_number.desc())
        .limit(1)
    )
    revision = CortisolPkParameterRevision(
        owner_id=owner_id,
        revision_number=1 if previous is None else previous.revision_number + 1,
        supersedes_id=None if previous is None else previous.id,
        elimination_half_life_hours=elimination_half_life_hours,
        peak_time_hours=peak_time_hours,
        distribution_volume_liters=distribution_volume_liters,
        oral_bioavailability=oral_bioavailability,
        source_revision=PARAMETER_SOURCE_REVISION,
    )
    # Validate before relying on database constraints so API callers receive a bounded
    # validation failure rather than a backend integrity error.
    parameters_from_revision(revision).validate()
    session.add(revision)
    session.flush()
    session.refresh(revision)
    return revision


def _normalized_shape(elapsed_hours: float, parameters: WakeFreeParameters) -> float:
    if elapsed_hours < 0.0 or elapsed_hours > HORIZON_HOURS:
        return 0.0
    ka = parameters.absorption_rate_per_hour
    ke = parameters.elimination_rate_per_hour
    peak_at = parameters.peak_time_hours
    if abs(ka - ke) <= 1e-8:
        raw = elapsed_hours * math.exp(-ke * elapsed_hours)
        peak = peak_at * math.exp(-ke * peak_at)
    else:
        raw = math.exp(-ke * elapsed_hours) - math.exp(-ka * elapsed_hours)
        peak = math.exp(-ke * peak_at) - math.exp(-ka * peak_at)
    result = raw / peak
    if not math.isfinite(result) or result < 0.0:
        raise ValueError("invalid wake-free PK result")
    return result


def concentration_nmol_per_liter(
    *,
    amount_mg: Decimal | float,
    elapsed_hours: float,
    parameters: WakeFreeParameters = DEFAULT_PARAMETERS,
) -> float:
    """Return one dose's serum-free-cortisol contribution in absolute units."""
    parameters.validate()
    amount = float(amount_mg)
    if not math.isfinite(amount) or amount < 0.0:
        raise ValueError("dose amount must be finite and nonnegative")
    if not math.isfinite(elapsed_hours):
        raise ValueError("elapsed time must be finite")
    if amount == 0.0:
        return 0.0
    return (
        parameters.free_peak_10_mg_nmol_l
        * (amount / 10.0)
        * _normalized_shape(elapsed_hours, parameters)
    )


def contribution_nmol_per_liter(
    dose: ExposureDose,
    instant: datetime,
    *,
    parameters: WakeFreeParameters = DEFAULT_PARAMETERS,
) -> float:
    if exclusion_reason(dose) is not None:
        return 0.0
    elapsed = (instant.astimezone(UTC) - dose.occurred_at.astimezone(UTC)).total_seconds() / 3600
    return concentration_nmol_per_liter(
        amount_mg=dose.amount,
        elapsed_hours=elapsed,
        parameters=parameters,
    )


def _display_decimal(value: float) -> Decimal:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("invalid wake-free PK result")
    result = Decimal(str(value)).quantize(DISPLAY_QUANTUM)
    return Decimal(0).quantize(DISPLAY_QUANTUM) if result < ZERO_RENDER_THRESHOLD else result


def _sample_instants(
    *,
    start: datetime,
    end: datetime,
    supported_doses: Iterable[ExposureDose],
    parameters: WakeFreeParameters,
) -> list[datetime]:
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    instants = {start_utc, end_utc}
    cursor = start_utc
    step = timedelta(minutes=SAMPLE_INTERVAL_MINUTES)
    while cursor < end_utc:
        instants.add(cursor)
        cursor += step
    for dose in supported_doses:
        administered = dose.occurred_at.astimezone(UTC)
        peak = administered + timedelta(hours=parameters.peak_time_hours)
        if start_utc <= administered <= end_utc:
            instants.add(administered)
        if start_utc <= peak <= end_utc:
            instants.add(peak)
    return sorted(instants)


def _revision_fingerprint(doses: Iterable[ExposureDose], parameters: WakeFreeParameters) -> str:
    parameter_token = {
        "revision_id": str(parameters.revision_id) if parameters.revision_id else None,
        "revision_number": parameters.revision_number,
        "half_life_hours": parameters.elimination_half_life_hours,
        "peak_time_hours": parameters.peak_time_hours,
        "distribution_volume_liters": parameters.distribution_volume_liters,
        "oral_bioavailability": parameters.oral_bioavailability,
        "source_revision": parameters.source_revision,
    }
    dose_tokens = [
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
            "binding_revision": BINDING_REVISION,
            "parameters": parameter_token,
            "doses": dose_tokens,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _parameter_metadata(parameters: WakeFreeParameters) -> dict[str, object]:
    return {
        "revision_id": parameters.revision_id,
        "revision_number": parameters.revision_number,
        "population_default": parameters.revision_id is None,
        "created_at": parameters.created_at,
        "source_revision": parameters.source_revision,
        "elimination_half_life_hours": Decimal(str(parameters.elimination_half_life_hours)),
        "peak_time_hours": Decimal(str(parameters.peak_time_hours)),
        "distribution_volume_liters": Decimal(str(parameters.distribution_volume_liters)),
        "oral_bioavailability": Decimal(str(parameters.oral_bioavailability)),
        "absorption_rate_per_hour": Decimal(str(parameters.absorption_rate_per_hour)),
        "elimination_rate_per_hour": Decimal(str(parameters.elimination_rate_per_hour)),
        "derived_clearance_liters_per_hour": Decimal(
            str(parameters.derived_clearance_liters_per_hour)
        ),
    }


def parameter_payload(parameters: WakeFreeParameters) -> dict[str, object]:
    return {
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "parameters": _parameter_metadata(parameters),
        "reference_defaults": {
            "absorption_duration_hours": Decimal(str(REFERENCE_ABSORPTION_DURATION_HOURS)),
            "clearance_liters_per_hour": Decimal(str(REFERENCE_CLEARANCE_LITERS_PER_HOUR)),
            "free_peak_10_mg_nmol_l": Decimal(str(DEFAULT_FREE_PEAK_10_MG_NMOL_L)),
            "calibration_revision": CALIBRATION_REVISION,
        },
    }


def build_curve(
    *,
    day: date,
    timezone: str,
    doses: list[ExposureDose],
    parameters: WakeFreeParameters = DEFAULT_PARAMETERS,
) -> dict[str, object]:
    """Build one local-day free-cortisol scenario from current dose facts."""
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
        parameters=parameters,
    ):
        regular_float = math.fsum(
            contribution_nmol_per_liter(dose, instant, parameters=parameters)
            for dose in supported
            if dose.category is not DoseCategory.STRESS
        )
        stress_float = math.fsum(
            contribution_nmol_per_liter(dose, instant, parameters=parameters)
            for dose in supported
            if dose.category is DoseCategory.STRESS
        )
        total_free_float = regular_float + stress_float
        regular_display = _display_decimal(regular_float)
        stress_display = _display_decimal(stress_float)
        local = instant.astimezone(zone)
        offset = local.utcoffset() or timedelta()
        samples.append(
            {
                "occurred_at": instant,
                "local_time": local.replace(tzinfo=None),
                "utc_offset_minutes": int(offset.total_seconds() // 60),
                "modeled_free_cortisol_nmol_l": regular_display + stress_display,
                "regular_modeled_free_cortisol_nmol_l": regular_display,
                "stress_modeled_free_cortisol_nmol_l": stress_display,
                "derived_total_cortisol_nmol_l_display": _display_decimal(
                    total_from_free(total_free_float)
                ),
            }
        )

    markers: list[dict[str, object]] = []
    for dose in ordered:
        reason = exclusion_reason(dose)
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
                    dose.occurred_at.astimezone(UTC) + timedelta(hours=parameters.peak_time_hours)
                    if reason is None
                    else None
                ),
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
            "Pointwise dose-proportional sum of current supported immediate-release oral "
            "hydrocortisone facts using concurrent absorption and first-order free-cortisol "
            "clearance. Derived total cortisol is display-only nonlinear binding context."
        ),
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "supported_medication": "hydrocortisone",
            "supported_formulation": "conventional immediate-release tablet",
            "supported_route": Route.ORAL,
            "amount_unit": DoseUnit.MG,
            "binding_revision": BINDING_REVISION,
            "calibration_revision": CALIBRATION_REVISION,
            "parameters": _parameter_metadata(parameters),
            "reference_absorption_duration_hours": Decimal(
                str(REFERENCE_ABSORPTION_DURATION_HOURS)
            ),
            "reference_clearance_liters_per_hour": Decimal(
                str(REFERENCE_CLEARANCE_LITERS_PER_HOUR)
            ),
            "free_peak_10_mg_nmol_l": Decimal(str(parameters.free_peak_10_mg_nmol_l)),
            "contribution_horizon_hours": HORIZON_HOURS,
            "sample_interval_minutes": SAMPLE_INTERVAL_MINUTES,
            "references": [
                "https://doi.org/10.1016/j.metabol.2017.02.005",
                "https://pubmed.ncbi.nlm.nih.gov/20528006/",
                "https://pubmed.ncbi.nlm.nih.gov/25369980/",
            ],
        },
        "source_revision_sha256": _revision_fingerprint(ordered, parameters),
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
    """Select current owner-scoped dose heads and the active parameter revision."""
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
    parameters = active_parameters(session, owner_id=owner_id)
    return build_curve(
        day=day,
        timezone=timezone,
        parameters=parameters,
        doses=[
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
        ],
    )
