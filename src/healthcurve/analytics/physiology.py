"""Deterministic selectable plasma-free-cortisol scenario from actual dose facts.

ADR-0024 defines this population-parameter forward model. Its output is a modeled
scenario, never a measured cortisol value, medication requirement, or dosing guide.
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
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.analytics.exposure import ExposureDose, exclusion_reason
from healthcurve.events import service as event_service
from healthcurve.medications.models import DoseCategory, DoseEvent, DoseUnit, Route

MODEL_ID: Final = "hc-physiology-v2"
MODEL_REVISION: Final = "hc-physiology-v2.0.0"
SERIES_KIND: Final = "modeled_plasma_free_cortisol_scenario"
SERIES_NAME: Final = "Modeled plasma-free-cortisol scenario"
SERIES_UNIT: Final = "nmol/L"
SAFETY_LABEL: Final = (
    "Population-parameter modeled plasma-free-cortisol scenario—not a measured cortisol "
    "value, personal target, medication-adequacy test, or dosing guide."
)
SAMPLE_INTERVAL_MINUTES: Final = 5
HORIZON_HOURS: Final = 48
DISPLAY_QUANTUM: Final = Decimal("0.000000001")
ZERO_RENDER_THRESHOLD: Final = Decimal("0.000000001")


@dataclass(frozen=True, slots=True)
class PhysiologyParameters:
    absorption_rate_per_hour: float = 1.4
    oral_bioavailability: float = 0.96
    clearance_liters_per_hour: float = 235.78
    distribution_volume_liters: float = 474.38
    cortisol_molecular_weight: float = 362.46

    def validate(self) -> None:
        values = (
            self.absorption_rate_per_hour,
            self.oral_bioavailability,
            self.clearance_liters_per_hour,
            self.distribution_volume_liters,
            self.cortisol_molecular_weight,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("invalid physiological scenario parameter set")
        if self.oral_bioavailability > 1.0:
            raise ValueError("oral bioavailability cannot exceed one")

    @property
    def elimination_rate_per_hour(self) -> float:
        self.validate()
        return self.clearance_liters_per_hour / self.distribution_volume_liters

    @property
    def elimination_half_life_hours(self) -> float:
        return math.log(2.0) / self.elimination_rate_per_hour

    @property
    def peak_time_hours(self) -> float:
        ka = self.absorption_rate_per_hour
        ke = self.elimination_rate_per_hour
        if abs(ka - ke) < 1e-6:
            return 1.0 / ke
        return math.log(ka / ke) / (ka - ke)


DEFAULT_PARAMETERS: Final = PhysiologyParameters()


def concentration_nmol_per_liter(
    *,
    amount_mg: Decimal | float,
    elapsed_hours: float,
    parameters: PhysiologyParameters = DEFAULT_PARAMETERS,
) -> float:
    """Return one oral dose's modeled plasma-free-cortisol contribution."""
    parameters.validate()
    amount = float(amount_mg)
    if not math.isfinite(amount) or amount < 0.0:
        raise ValueError("dose amount must be finite and nonnegative")
    if not math.isfinite(elapsed_hours):
        raise ValueError("elapsed time must be finite")
    if elapsed_hours < 0.0 or elapsed_hours > HORIZON_HOURS or amount == 0.0:
        return 0.0

    ka = parameters.absorption_rate_per_hour
    ke = parameters.elimination_rate_per_hour
    scale = (
        parameters.oral_bioavailability
        * amount
        / parameters.distribution_volume_liters
        * (1_000_000.0 / parameters.cortisol_molecular_weight)
    )
    if abs(ka - ke) < 1e-6:
        result = scale * ke * elapsed_hours * math.exp(-ke * elapsed_hours)
    else:
        result = (
            scale * ka / (ka - ke) * (math.exp(-ke * elapsed_hours) - math.exp(-ka * elapsed_hours))
        )
    if not math.isfinite(result) or result < 0.0:
        raise ValueError("invalid physiological scenario result")
    return result


def contribution_nmol_per_liter(
    dose: ExposureDose,
    instant: datetime,
    *,
    parameters: PhysiologyParameters = DEFAULT_PARAMETERS,
) -> float:
    if exclusion_reason(dose) is not None:
        return 0.0
    elapsed = (instant.astimezone(UTC) - dose.occurred_at.astimezone(UTC)).total_seconds() / 3600
    return concentration_nmol_per_liter(
        amount_mg=dose.amount, elapsed_hours=elapsed, parameters=parameters
    )


def modeled_free_cortisol_at(
    doses: Iterable[ExposureDose],
    instant: datetime,
    *,
    parameters: PhysiologyParameters = DEFAULT_PARAMETERS,
) -> Decimal:
    return _display_decimal(
        math.fsum(
            contribution_nmol_per_liter(dose, instant, parameters=parameters) for dose in doses
        )
    )


def _display_decimal(value: float) -> Decimal:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("invalid physiological scenario result")
    result = Decimal(str(value)).quantize(DISPLAY_QUANTUM)
    return Decimal(0).quantize(DISPLAY_QUANTUM) if result < ZERO_RENDER_THRESHOLD else result


def _sample_instants(
    *,
    start: datetime,
    end: datetime,
    supported_doses: Iterable[ExposureDose],
    parameters: PhysiologyParameters,
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


def _revision_fingerprint(doses: Iterable[ExposureDose]) -> str:
    tokens = []
    for dose in doses:
        tokens.append(
            {
                "id": str(dose.id),
                "occurred_at": dose.occurred_at.astimezone(UTC).isoformat(),
                "recorded_at": (
                    dose.recorded_at.astimezone(UTC).isoformat()
                    if dose.recorded_at is not None
                    else None
                ),
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
        )
    body = json.dumps(
        {"model_revision": MODEL_REVISION, "doses": tokens},
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
    parameters: PhysiologyParameters = DEFAULT_PARAMETERS,
) -> dict[str, object]:
    """Build one local-day population-parameter scenario from current dose facts."""
    parameters.validate()
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    ordered = sorted(doses, key=lambda dose: (dose.occurred_at.astimezone(UTC), dose.id))
    supported = [dose for dose in ordered if exclusion_reason(dose) is None]

    samples: list[dict[str, object]] = []
    for instant in _sample_instants(
        start=start, end=end, supported_doses=supported, parameters=parameters
    ):
        regular = _display_decimal(
            math.fsum(
                contribution_nmol_per_liter(dose, instant, parameters=parameters)
                for dose in supported
                if dose.category is not DoseCategory.STRESS
            )
        )
        stress = _display_decimal(
            math.fsum(
                contribution_nmol_per_liter(dose, instant, parameters=parameters)
                for dose in supported
                if dose.category is DoseCategory.STRESS
            )
        )
        local = instant.astimezone(zone)
        offset = local.utcoffset()
        samples.append(
            {
                "occurred_at": instant,
                "local_time": local.replace(tzinfo=None),
                "utc_offset_minutes": int((offset or timedelta()).total_seconds() // 60),
                "modeled_free_cortisol_nmol_l": regular + stress,
                "regular_modeled_free_cortisol_nmol_l": regular,
                "stress_modeled_free_cortisol_nmol_l": stress,
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
            "Pointwise sum of current supported immediate-release oral hydrocortisone dose "
            "facts using the ADR-0024 direct plasma-free-cortisol population model. The "
            "result is a modeled scenario, not a measurement or medication-need estimate."
        ),
        "model": {
            "id": MODEL_ID,
            "revision": MODEL_REVISION,
            "supported_medication": "hydrocortisone",
            "supported_formulation": "conventional immediate-release tablet",
            "supported_route": Route.ORAL,
            "amount_unit": DoseUnit.MG,
            "absorption_rate_per_hour": Decimal(str(parameters.absorption_rate_per_hour)),
            "oral_bioavailability": Decimal(str(parameters.oral_bioavailability)),
            "clearance_liters_per_hour": Decimal(str(parameters.clearance_liters_per_hour)),
            "distribution_volume_liters": Decimal(str(parameters.distribution_volume_liters)),
            "cortisol_molecular_weight": Decimal(str(parameters.cortisol_molecular_weight)),
            "elimination_half_life_hours": Decimal(str(parameters.elimination_half_life_hours)),
            "elimination_rate_per_hour": Decimal(str(parameters.elimination_rate_per_hour)),
            "peak_time_hours": Decimal(str(parameters.peak_time_hours)),
            "contribution_horizon_hours": HORIZON_HOURS,
            "sample_interval_minutes": SAMPLE_INTERVAL_MINUTES,
            "references": [
                "https://doi.org/10.1016/j.metabol.2017.02.005",
                "https://doi.org/10.2165/11531290-000000000-00000",
                "https://doi.org/10.1002/j.1552-4604.1991.tb01906.x",
            ],
        },
        "source_revision_sha256": _revision_fingerprint(ordered),
        "dose_markers": markers,
        "samples": samples,
        "supported_dose_count": len(supported),
        "excluded_dose_count": len(ordered) - len(supported),
    }


def curve_for_owner(
    session: Session, *, owner_id: uuid.UUID, day: date, timezone: str
) -> dict[str, object]:
    """Select owner-scoped correction-chain heads within the 48-hour lookback."""
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
    return build_curve(
        day=day,
        timezone=timezone,
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
