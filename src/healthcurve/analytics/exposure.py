"""Deterministic theoretical hydrocortisone exposure from current dose facts.

The output is a normalized visualization index, never a measured cortisol value or a
dosing recommendation. ADR-0013 defines the model, support boundary, and gold cases.
"""

from __future__ import annotations

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

from healthcurve.events import service as event_service
from healthcurve.medications.models import DoseCategory, DoseEvent, DoseUnit, Route

MODEL_VERSION: Final = "hc-exposure-v1"
SERIES_NAME: Final = "Theoretical hydrocortisone exposure"
SERIES_UNIT: Final = "REU"
SAFETY_LABEL: Final = (
    "Theoretical hydrocortisone exposure—not a cortisol measurement or dosing guide. "
    "Absorption and clearance vary substantially between people and circumstances."
)
ABSORPTION_RATE_PER_HOUR: Final = 2.0
ELIMINATION_HALF_LIFE_HOURS: Final = 1.7
ELIMINATION_RATE_PER_HOUR: Final = math.log(2.0) / ELIMINATION_HALF_LIFE_HOURS
PEAK_TIME_HOURS: Final = math.log(ABSORPTION_RATE_PER_HOUR / ELIMINATION_RATE_PER_HOUR) / (
    ABSORPTION_RATE_PER_HOUR - ELIMINATION_RATE_PER_HOUR
)
HORIZON_HOURS: Final = 24
SAMPLE_INTERVAL_MINUTES: Final = 5
_DISPLAY_QUANTUM: Final = Decimal("0.000000001")
_SUPPORTED_FORMULATIONS: Final = frozenset(
    {
        "tablet",
        "immediate release tablet",
        "immediate-release tablet",
        "conventional immediate-release tablet",
    }
)


@dataclass(frozen=True, slots=True)
class ExposureDose:
    id: uuid.UUID
    occurred_at: datetime
    local_time: datetime
    timezone: str
    utc_offset_minutes: int
    amount: Decimal
    unit: DoseUnit
    route: Route
    category: DoseCategory
    medication_name: str
    normalized_medication_name: str
    formulation: str | None
    source_type: str
    confirmation_state: str
    supersedes_id: uuid.UUID | None


def _normalized_formulation(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.casefold().strip().split())


def exclusion_reason(dose: ExposureDose) -> str | None:
    """Return a stable reason code instead of guessing parameters."""
    if dose.normalized_medication_name.casefold().strip() != "hydrocortisone":
        return "unsupported_medication"
    if _normalized_formulation(dose.formulation) not in _SUPPORTED_FORMULATIONS:
        return "unsupported_formulation"
    if dose.route != Route.ORAL:
        return "unsupported_route"
    if dose.unit != DoseUnit.MG:
        return "unsupported_unit"
    return None


def normalized_shape(elapsed_hours: float) -> float:
    """Return the ADR-0013 unit-peak absorption/elimination shape."""
    if elapsed_hours < 0.0 or elapsed_hours > HORIZON_HOURS:
        return 0.0
    raw = math.exp(-ELIMINATION_RATE_PER_HOUR * elapsed_hours) - math.exp(
        -ABSORPTION_RATE_PER_HOUR * elapsed_hours
    )
    peak = math.exp(-ELIMINATION_RATE_PER_HOUR * PEAK_TIME_HOURS) - math.exp(
        -ABSORPTION_RATE_PER_HOUR * PEAK_TIME_HOURS
    )
    result = raw / peak
    if not math.isfinite(result) or result < 0.0:
        raise ValueError("invalid theoretical exposure parameter set")
    return result


def contribution_reu(dose: ExposureDose, instant: datetime) -> float:
    if exclusion_reason(dose) is not None:
        return 0.0
    elapsed = (instant.astimezone(UTC) - dose.occurred_at.astimezone(UTC)).total_seconds() / 3600
    return float(dose.amount) * normalized_shape(elapsed)


def exposure_reu_at(doses: Iterable[ExposureDose], instant: datetime) -> Decimal:
    """Evaluate the versioned exposure model directly at one instant."""
    return _display_decimal(math.fsum(contribution_reu(dose, instant) for dose in doses))


def _display_decimal(value: float) -> Decimal:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("invalid theoretical exposure result")
    return Decimal(str(value)).quantize(_DISPLAY_QUANTUM)


def _sample_instants(
    *, start: datetime, end: datetime, supported_doses: Iterable[ExposureDose]
) -> list[datetime]:
    instants = {start.astimezone(UTC), end.astimezone(UTC)}
    cursor = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    step = timedelta(minutes=SAMPLE_INTERVAL_MINUTES)
    while cursor < end_utc:
        instants.add(cursor)
        cursor += step
    for dose in supported_doses:
        administered = dose.occurred_at.astimezone(UTC)
        peak = administered + timedelta(hours=PEAK_TIME_HOURS)
        if start.astimezone(UTC) <= administered <= end_utc:
            instants.add(administered)
        if start.astimezone(UTC) <= peak <= end_utc:
            instants.add(peak)
    return sorted(instants)


def build_curve(*, day: date, timezone: str, doses: list[ExposureDose]) -> dict[str, object]:
    """Build one local-day curve from already owner-scoped current dose facts."""
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    ordered = sorted(doses, key=lambda dose: (dose.occurred_at.astimezone(UTC), dose.id))
    supported = [dose for dose in ordered if exclusion_reason(dose) is None]
    samples = []
    for instant in _sample_instants(start=start, end=end, supported_doses=supported):
        regular = _display_decimal(
            math.fsum(
                contribution_reu(dose, instant)
                for dose in supported
                if dose.category is not DoseCategory.STRESS
            )
        )
        stress = _display_decimal(
            math.fsum(
                contribution_reu(dose, instant)
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
                "theoretical_exposure_reu": regular + stress,
                "regular_exposure_reu": regular,
                "stress_exposure_reu": stress,
            }
        )

    markers = []
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
                    dose.occurred_at.astimezone(UTC) + timedelta(hours=PEAK_TIME_HOURS)
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
        "series_name": SERIES_NAME,
        "series_unit": SERIES_UNIT,
        "safety_label": SAFETY_LABEL,
        "definition": (
            "Pointwise sum of current supported recorded doses using the versioned "
            "ADR-0013 absorption/elimination shape. Missing and unsupported doses are "
            "never converted to zero-valued facts. Explicit stress doses form the stress "
            "component; scheduled, late, replacement, taper, and emergency categories "
            "are grouped into the regular component so every supported dose is counted once."
        ),
        "model": {
            "version": MODEL_VERSION,
            "supported_medication": "hydrocortisone",
            "supported_formulation": "conventional immediate-release tablet",
            "supported_route": Route.ORAL,
            "amount_unit": DoseUnit.MG,
            "absorption_rate_per_hour": Decimal(str(ABSORPTION_RATE_PER_HOUR)),
            "elimination_half_life_hours": Decimal(str(ELIMINATION_HALF_LIFE_HOURS)),
            "elimination_rate_per_hour": Decimal(str(ELIMINATION_RATE_PER_HOUR)),
            "peak_time_hours": Decimal(str(PEAK_TIME_HOURS)),
            "contribution_horizon_hours": HORIZON_HOURS,
            "sample_interval_minutes": SAMPLE_INTERVAL_MINUTES,
            "references": [
                "https://doi.org/10.1002/j.1552-4604.1991.tb01906.x",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC5963674/",
                "https://doi.org/10.1016/j.metabol.2017.02.005",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC4880116/",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC9231005/",
            ],
        },
        "dose_markers": markers,
        "samples": samples,
        "supported_dose_count": len(supported),
        "excluded_dose_count": len(ordered) - len(supported),
    }


def curve_for_owner(
    session: Session, *, owner_id: uuid.UUID, day: date, timezone: str
) -> dict[str, object]:
    """Select current owner facts, including the preceding contribution horizon."""
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
            )
            for row in current
        ],
    )
