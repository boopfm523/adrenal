"""Deterministic selected-day features for the wake-anchored cortisol model.

The values in this module are descriptive projections over modeled and population-
reference series.  They are not recorded facts, cortisol measurements, adequacy
judgments, alerts, or dosing guidance.  ADR-0026 defines the safety boundary.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Final, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.events import service as event_service
from healthcurve.events.models import SymptomEvent
from healthcurve.medications.models import DoseCategory

FEATURE_ID: Final = "hc-wake-coverage-v1"
FEATURE_REVISION: Final = "hc-wake-coverage-v1.0.0"
DISPLAY_QUANTUM: Final = Decimal("0.0001")
SAFETY_LABEL: Final = (
    "Descriptive comparison of a modeled serum-free-cortisol scenario with a broad "
    "healthy-adult population reference. It is not a measurement, personal target, "
    "medication-adequacy test, alert, or dosing guide."
)
DEFINITIONS: Final = {
    "comparison_window": (
        "Historical days use their full real elapsed duration. The current local day ends "
        "at the calculation instant; future time is not silently counted as observed."
    ),
    "below_band": (
        "Piecewise-linear elapsed time where modeled serum free cortisol is below the "
        "reference P5 or P25. Expected sleep/pre-first-dose intervals are excluded and "
        "reported separately. Position outside a population percentile is neutral context."
    ),
    "auc": (
        "Trapezoidal area under each serum-free-cortisol series over the comparison window. "
        "Regular and explicitly recorded stress-dose contributions remain separate."
    ),
    "inter_dose_troughs": (
        "The minimum modeled value between consecutive supported doses administered during "
        "the selected local day, with contemporaneous reference values."
    ),
    "maximum_fall": (
        "The largest positive magnitude of a negative piecewise-linear modeled slope, in "
        "nmol/L per elapsed hour."
    ),
    "symptom_context": (
        "Time since the latest supported recorded dose and interpolated modeled/reference "
        "values at each current symptom fact. Temporal proximity does not establish cause."
    ),
    "p95_overshoot": (
        "Piecewise-linear elapsed time and maximum magnitude above the healthy-reference P95. "
        "This is descriptive context, not an urgency or medication signal."
    ),
}


@dataclass(frozen=True, slots=True)
class FeatureSample:
    occurred_at: datetime
    modeled: Decimal
    regular: Decimal
    stress: Decimal
    p5: Decimal
    p25: Decimal
    p50: Decimal
    p95: Decimal


@dataclass(frozen=True, slots=True)
class FeatureDose:
    dose_event_id: uuid.UUID
    occurred_at: datetime
    category: str


@dataclass(frozen=True, slots=True)
class FeatureSymptom:
    symptom_event_id: uuid.UUID
    occurred_at: datetime
    name: str
    severity: int | None
    source_revision: str | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("feature timestamps must include a timezone")
    return value.astimezone(UTC)


def _display(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(DISPLAY_QUANTUM)


def _interpolate(left: FeatureSample, right: FeatureSample, at: datetime) -> FeatureSample:
    instant = _utc(at)
    left_at = _utc(left.occurred_at)
    right_at = _utc(right.occurred_at)
    if not left_at <= instant <= right_at or right_at <= left_at:
        raise ValueError("interpolation instant must fall within a positive sample interval")
    fraction = Decimal(
        str((instant - left_at).total_seconds() / (right_at - left_at).total_seconds())
    )

    def value(field: str) -> Decimal:
        left_value = cast(Decimal, getattr(left, field))
        right_value = cast(Decimal, getattr(right, field))
        return left_value + (right_value - left_value) * fraction

    return FeatureSample(
        occurred_at=instant,
        modeled=value("modeled"),
        regular=value("regular"),
        stress=value("stress"),
        p5=value("p5"),
        p25=value("p25"),
        p50=value("p50"),
        p95=value("p95"),
    )


def _sample_at(samples: Sequence[FeatureSample], at: datetime) -> FeatureSample | None:
    instant = _utc(at)
    if (
        not samples
        or instant < _utc(samples[0].occurred_at)
        or instant > _utc(samples[-1].occurred_at)
    ):
        return None
    for left, right in pairwise(samples):
        left_at = _utc(left.occurred_at)
        right_at = _utc(right.occurred_at)
        if instant == left_at:
            return left
        if left_at < instant < right_at:
            return _interpolate(left, right, instant)
    return samples[-1] if instant == _utc(samples[-1].occurred_at) else None


def _clip_samples(
    samples: Sequence[FeatureSample], *, start: datetime, end: datetime
) -> list[FeatureSample]:
    start_utc = _utc(start)
    end_utc = _utc(end)
    if end_utc <= start_utc:
        return []
    start_sample = _sample_at(samples, start_utc)
    end_sample = _sample_at(samples, end_utc)
    if start_sample is None or end_sample is None:
        return []
    middle = [sample for sample in samples if start_utc < _utc(sample.occurred_at) < end_utc]
    return [start_sample, *middle, end_sample]


def _merge_intervals(
    intervals: Iterable[tuple[datetime, datetime]], *, start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    start_utc = _utc(start)
    end_utc = _utc(end)
    clipped = sorted(
        (
            (max(start_utc, _utc(left)), min(end_utc, _utc(right)))
            for left, right in intervals
            if _utc(right) > start_utc and _utc(left) < end_utc
        ),
        key=lambda value: value[0],
    )
    merged: list[tuple[datetime, datetime]] = []
    for left, right in clipped:
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return merged


def _allowed_pieces(
    left: datetime,
    right: datetime,
    exclusions: Sequence[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    pieces = [(_utc(left), _utc(right))]
    for excluded_left, excluded_right in exclusions:
        next_pieces: list[tuple[datetime, datetime]] = []
        for piece_left, piece_right in pieces:
            if excluded_right <= piece_left or excluded_left >= piece_right:
                next_pieces.append((piece_left, piece_right))
                continue
            if piece_left < excluded_left:
                next_pieces.append((piece_left, excluded_left))
            if excluded_right < piece_right:
                next_pieces.append((excluded_right, piece_right))
        pieces = next_pieces
    return pieces


def _duration_negative_hours(left: Decimal, right: Decimal, duration_hours: Decimal) -> Decimal:
    if left < 0 and right < 0:
        return duration_hours
    if left >= 0 and right >= 0:
        return Decimal(0)
    crossing = abs(left) / (abs(left) + abs(right))
    return duration_hours * (crossing if left < 0 else Decimal(1) - crossing)


def _duration_positive_hours(left: Decimal, right: Decimal, duration_hours: Decimal) -> Decimal:
    return _duration_negative_hours(-left, -right, duration_hours)


def _duration_against(
    samples: Sequence[FeatureSample],
    *,
    threshold: str,
    below: bool,
    exclusions: Sequence[tuple[datetime, datetime]] = (),
) -> Decimal:
    result = Decimal(0)
    for left, right in pairwise(samples):
        for piece_start, piece_end in _allowed_pieces(
            left.occurred_at, right.occurred_at, exclusions
        ):
            piece_left = _interpolate(left, right, piece_start)
            piece_right = _interpolate(left, right, piece_end)
            left_delta = piece_left.modeled - cast(Decimal, getattr(piece_left, threshold))
            right_delta = piece_right.modeled - cast(Decimal, getattr(piece_right, threshold))
            duration = Decimal(str((piece_end - piece_start).total_seconds() / 3600))
            result += (
                _duration_negative_hours(left_delta, right_delta, duration)
                if below
                else _duration_positive_hours(left_delta, right_delta, duration)
            )
    return result


def _auc(samples: Sequence[FeatureSample], field: str) -> Decimal:
    result = Decimal(0)
    for left, right in pairwise(samples):
        hours = Decimal(
            str((_utc(right.occurred_at) - _utc(left.occurred_at)).total_seconds() / 3600)
        )
        result += (
            (cast(Decimal, getattr(left, field)) + cast(Decimal, getattr(right, field)))
            / Decimal(2)
            * hours
        )
    return result


def _expected_pre_wake_intervals(
    *,
    start: datetime,
    end: datetime,
    wake_at: datetime | None,
    sleep_onset_at: datetime | None,
    day_doses: Sequence[FeatureDose],
) -> list[tuple[datetime, datetime]]:
    if wake_at is None:
        return []
    start_utc = _utc(start)
    end_utc = _utc(end)
    wake_utc = min(end_utc, max(start_utc, _utc(wake_at)))
    first_dose = min(
        (_utc(dose.occurred_at) for dose in day_doses if _utc(dose.occurred_at) >= start_utc),
        default=None,
    )
    morning_end = wake_utc
    if first_dose is not None and first_dose <= wake_utc + timedelta(hours=6):
        morning_end = min(end_utc, max(wake_utc, first_dose))
    intervals: list[tuple[datetime, datetime]] = []
    if morning_end > start_utc:
        intervals.append((start_utc, morning_end))
    if sleep_onset_at is not None:
        sleep_utc = _utc(sleep_onset_at)
        if start_utc <= sleep_utc < end_utc and sleep_utc > wake_utc:
            intervals.append((sleep_utc, end_utc))
    return _merge_intervals(intervals, start=start_utc, end=end_utc)


def _fingerprint(
    *,
    source_model_sha256: str,
    reference_revision: str,
    samples: Sequence[FeatureSample],
    doses: Sequence[FeatureDose],
    symptoms: Sequence[FeatureSymptom],
    analyzed_through: datetime,
) -> str:
    body = {
        "feature_revision": FEATURE_REVISION,
        "source_model_sha256": source_model_sha256,
        "reference_revision": reference_revision,
        "analyzed_through": _utc(analyzed_through).isoformat(),
        "samples": [
            (
                _utc(sample.occurred_at).isoformat(),
                str(sample.modeled),
                str(sample.regular),
                str(sample.stress),
                str(sample.p5),
                str(sample.p25),
                str(sample.p50),
                str(sample.p95),
            )
            for sample in samples
        ],
        "doses": [
            (str(dose.dose_event_id), _utc(dose.occurred_at).isoformat(), dose.category)
            for dose in doses
        ],
        "symptoms": [
            (
                str(symptom.symptom_event_id),
                _utc(symptom.occurred_at).isoformat(),
                symptom.name,
                symptom.severity,
                symptom.source_revision,
            )
            for symptom in symptoms
        ],
    }
    encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _troughs(
    samples: Sequence[FeatureSample], doses: Sequence[FeatureDose]
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for previous, following in pairwise(doses):
        previous_at = _utc(previous.occurred_at)
        following_at = _utc(following.occurred_at)
        if following_at <= previous_at:
            continue
        candidates = [
            sample for sample in samples if previous_at <= _utc(sample.occurred_at) <= following_at
        ]
        for instant in (previous_at, following_at):
            exact = _sample_at(samples, instant)
            if exact is not None:
                candidates.append(exact)
        if not candidates:
            continue
        trough = min(candidates, key=lambda sample: (sample.modeled, _utc(sample.occurred_at)))
        result.append(
            {
                "previous_dose_event_id": previous.dose_event_id,
                "next_dose_event_id": following.dose_event_id,
                "occurred_at": _utc(trough.occurred_at),
                "modeled_free_cortisol_nmol_l": _display(trough.modeled),
                "regular_modeled_free_cortisol_nmol_l": _display(trough.regular),
                "stress_modeled_free_cortisol_nmol_l": _display(trough.stress),
                "reference_p5_nmol_l": _display(trough.p5),
                "reference_p25_nmol_l": _display(trough.p25),
                "reference_p50_nmol_l": _display(trough.p50),
                "depth_below_p50_nmol_l": _display(max(Decimal(0), trough.p50 - trough.modeled)),
            }
        )
    return result


def _maximum_fall(samples: Sequence[FeatureSample]) -> dict[str, object] | None:
    candidates: list[tuple[Decimal, FeatureSample, FeatureSample]] = []
    for left, right in pairwise(samples):
        hours = Decimal(
            str((_utc(right.occurred_at) - _utc(left.occurred_at)).total_seconds() / 3600)
        )
        if hours <= 0:
            continue
        slope = (right.modeled - left.modeled) / hours
        if slope < 0:
            candidates.append((-slope, left, right))
    if not candidates:
        return None
    magnitude, left, right = max(
        candidates, key=lambda value: (value[0], -_utc(value[1].occurred_at).timestamp())
    )
    return {
        "magnitude_nmol_l_per_hour": _display(magnitude),
        "interval_started_at": _utc(left.occurred_at),
        "interval_ended_at": _utc(right.occurred_at),
        "from_modeled_free_cortisol_nmol_l": _display(left.modeled),
        "to_modeled_free_cortisol_nmol_l": _display(right.modeled),
    }


def _symptom_contexts(
    samples: Sequence[FeatureSample],
    doses: Sequence[FeatureDose],
    symptoms: Sequence[FeatureSymptom],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for symptom in sorted(
        symptoms, key=lambda value: (_utc(value.occurred_at), value.symptom_event_id)
    ):
        at = _utc(symptom.occurred_at)
        sample = _sample_at(samples, at)
        if sample is None:
            continue
        previous = [dose for dose in doses if _utc(dose.occurred_at) <= at]
        latest_at = max((_utc(dose.occurred_at) for dose in previous), default=None)
        latest = (
            []
            if latest_at is None
            else [dose for dose in previous if _utc(dose.occurred_at) == latest_at]
        )
        minutes_since = (
            None
            if latest_at is None
            else _display(Decimal(str((at - latest_at).total_seconds() / 60)))
        )
        result.append(
            {
                "symptom_event_id": symptom.symptom_event_id,
                "occurred_at": at,
                "name": symptom.name,
                "severity": symptom.severity,
                "previous_supported_dose_event_ids": sorted(
                    (dose.dose_event_id for dose in latest), key=str
                ),
                "previous_dose_categories": sorted({dose.category for dose in latest}),
                "minutes_since_previous_supported_dose": minutes_since,
                "modeled_free_cortisol_nmol_l": _display(sample.modeled),
                "regular_modeled_free_cortisol_nmol_l": _display(sample.regular),
                "stress_modeled_free_cortisol_nmol_l": _display(sample.stress),
                "reference_p5_nmol_l": _display(sample.p5),
                "reference_p50_nmol_l": _display(sample.p50),
                "reference_p95_nmol_l": _display(sample.p95),
            }
        )
    return result


def derive_daily_features(
    *,
    day: date,
    timezone: str,
    day_start: datetime,
    day_end: datetime,
    analyzed_through: datetime,
    samples: Sequence[FeatureSample],
    doses: Sequence[FeatureDose],
    symptoms: Sequence[FeatureSymptom],
    wake_at: datetime | None,
    sleep_onset_at: datetime | None,
    source_model_sha256: str,
    reference_revision: str,
) -> dict[str, object]:
    """Derive one reproducible feature projection from already aligned series."""

    start = _utc(day_start)
    full_end = _utc(day_end)
    through = min(full_end, _utc(analyzed_through))
    ordered_samples = sorted(samples, key=lambda sample: _utc(sample.occurred_at))
    ordered_doses = sorted(doses, key=lambda dose: (_utc(dose.occurred_at), dose.dose_event_id))
    clipped = _clip_samples(ordered_samples, start=start, end=through)
    base = {
        "available": len(clipped) >= 2,
        "feature_id": FEATURE_ID,
        "feature_revision": FEATURE_REVISION,
        "date": day,
        "timezone": timezone,
        "analyzed_from": start,
        "analyzed_through": through,
        "elapsed_hours": _display(
            max(Decimal(0), Decimal(str((through - start).total_seconds() / 3600)))
        ),
        "day_state": "complete" if through >= full_end else "partial",
        "safety_label": SAFETY_LABEL,
        "definitions": DEFINITIONS,
        "missing_inputs": [] if len(clipped) >= 2 else ["elapsed_comparison_window"],
    }
    if len(clipped) < 2:
        return {
            **base,
            "source_revision_sha256": _fingerprint(
                source_model_sha256=source_model_sha256,
                reference_revision=reference_revision,
                samples=clipped,
                doses=ordered_doses,
                symptoms=symptoms,
                analyzed_through=through,
            ),
            "comparison_minutes": None,
            "expected_pre_wake_excluded_minutes": None,
            "time_below_p5_minutes": None,
            "time_below_p25_minutes": None,
            "auc": None,
            "inter_dose_troughs": [],
            "maximum_fall": None,
            "p95_overshoot": None,
            "symptom_contexts": [],
        }

    day_doses = [dose for dose in ordered_doses if start <= _utc(dose.occurred_at) < through]
    exclusions = _expected_pre_wake_intervals(
        start=start,
        end=through,
        wake_at=wake_at,
        sleep_onset_at=sleep_onset_at,
        day_doses=day_doses,
    )
    excluded_hours = sum(
        (Decimal(str((right - left).total_seconds() / 3600)) for left, right in exclusions),
        Decimal(0),
    )
    elapsed_hours = Decimal(str((through - start).total_seconds() / 3600))
    modeled_auc = _auc(clipped, "modeled")
    regular_auc = _auc(clipped, "regular")
    stress_auc = _auc(clipped, "stress")
    reference_auc = _auc(clipped, "p50")
    overshoots = [(sample.modeled - sample.p95, sample) for sample in clipped]
    maximum_overshoot, overshoot_sample = max(
        overshoots, key=lambda value: (value[0], -_utc(value[1].occurred_at).timestamp())
    )
    if maximum_overshoot <= 0:
        overshoot_at = None
        maximum_overshoot = Decimal(0)
    else:
        overshoot_at = _utc(overshoot_sample.occurred_at)

    return {
        **base,
        "source_revision_sha256": _fingerprint(
            source_model_sha256=source_model_sha256,
            reference_revision=reference_revision,
            samples=clipped,
            doses=ordered_doses,
            symptoms=symptoms,
            analyzed_through=through,
        ),
        "comparison_minutes": _display((elapsed_hours - excluded_hours) * Decimal(60)),
        "expected_pre_wake_excluded_minutes": _display(excluded_hours * Decimal(60)),
        "time_below_p5_minutes": _display(
            _duration_against(clipped, threshold="p5", below=True, exclusions=exclusions)
            * Decimal(60)
        ),
        "time_below_p25_minutes": _display(
            _duration_against(clipped, threshold="p25", below=True, exclusions=exclusions)
            * Decimal(60)
        ),
        "auc": {
            "modeled_free_nmol_l_hours": _display(modeled_auc),
            "regular_modeled_free_nmol_l_hours": _display(regular_auc),
            "stress_modeled_free_nmol_l_hours": _display(stress_auc),
            "reference_p50_nmol_l_hours": _display(reference_auc),
            "modeled_minus_reference_p50_nmol_l_hours": _display(modeled_auc - reference_auc),
            "modeled_to_reference_p50_ratio": (
                None if reference_auc == 0 else _display(modeled_auc / reference_auc)
            ),
        },
        "inter_dose_troughs": _troughs(clipped, day_doses),
        "maximum_fall": _maximum_fall(clipped),
        "p95_overshoot": {
            "duration_minutes": _display(
                _duration_against(clipped, threshold="p95", below=False) * Decimal(60)
            ),
            "maximum_nmol_l": _display(maximum_overshoot),
            "maximum_at": overshoot_at,
        },
        "symptom_contexts": _symptom_contexts(clipped, ordered_doses, symptoms),
    }


def _feature_samples(
    curve: Mapping[str, object], reference: Mapping[str, object]
) -> list[FeatureSample]:
    curve_samples = cast(list[dict[str, object]], curve.get("samples", []))
    reference_samples = cast(list[dict[str, object]], reference.get("samples", []))
    reference_by_at = {
        _utc(cast(datetime, sample["occurred_at"])): sample for sample in reference_samples
    }
    result = []
    for sample in curve_samples:
        occurred_at = _utc(cast(datetime, sample["occurred_at"]))
        matched = reference_by_at.get(occurred_at)
        if matched is None:
            continue
        result.append(
            FeatureSample(
                occurred_at=occurred_at,
                modeled=cast(Decimal, sample["modeled_free_cortisol_nmol_l"]),
                regular=cast(Decimal, sample["regular_modeled_free_cortisol_nmol_l"]),
                stress=cast(Decimal, sample["stress_modeled_free_cortisol_nmol_l"]),
                p5=cast(Decimal, matched["serum_free_p5_nmol_l"]),
                p25=cast(Decimal, matched["serum_free_p25_nmol_l"]),
                p50=cast(Decimal, matched["serum_free_p50_nmol_l"]),
                p95=cast(Decimal, matched["serum_free_p95_nmol_l"]),
            )
        )
    return result


def _feature_doses(curve: Mapping[str, object]) -> list[FeatureDose]:
    result = []
    for marker in cast(list[dict[str, object]], curve.get("dose_markers", [])):
        if marker.get("supported") is not True:
            continue
        category = marker["category"]
        result.append(
            FeatureDose(
                dose_event_id=cast(uuid.UUID, marker["dose_event_id"]),
                occurred_at=cast(datetime, marker["occurred_at"]),
                category=category.value if isinstance(category, DoseCategory) else str(category),
            )
        )
    return result


def _symptoms_for_owner(
    session: Session,
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[FeatureSymptom]:
    rows = list(
        session.scalars(
            select(SymptomEvent)
            .where(
                SymptomEvent.owner_id == owner_id,
                SymptomEvent.occurred_at >= start,
                SymptomEvent.occurred_at < end,
                event_service.current_fact_predicate(SymptomEvent, owner_id=owner_id),
            )
            .order_by(SymptomEvent.occurred_at, SymptomEvent.id)
        )
    )
    return [
        FeatureSymptom(
            symptom_event_id=row.id,
            occurred_at=row.occurred_at,
            name=row.name,
            severity=row.severity,
            source_revision=row.source_revision,
        )
        for row in rows
    ]


def features_for_owner(
    session: Session,
    *,
    owner_id: uuid.UUID,
    curve: Mapping[str, object],
    reference: Mapping[str, object],
    calculated_at: datetime | None = None,
) -> dict[str, object]:
    """Build current owner-scoped features from an already generated v3 response."""

    day = cast(date, curve["date"])
    timezone = cast(str, curve["timezone"])
    day_start = _utc(cast(datetime, curve["day_start"]))
    day_end = _utc(cast(datetime, curve["day_end"]))
    now = _utc(calculated_at or datetime.now(UTC))
    through = min(day_end, now)
    reference_identity = cast(dict[str, object], reference["reference"])
    reference_revision = str(reference_identity["revision"])
    assumptions = cast(dict[str, object] | None, reference.get("assumptions"))
    available = reference.get("available") is True
    samples = _feature_samples(curve, reference) if available else []
    doses = _feature_doses(curve)
    symptoms = (
        _symptoms_for_owner(
            session,
            owner_id=owner_id,
            start=day_start,
            end=max(day_start, through),
        )
        if through > day_start
        else []
    )
    result = derive_daily_features(
        day=day,
        timezone=timezone,
        day_start=day_start,
        day_end=day_end,
        analyzed_through=through,
        samples=samples,
        doses=doses,
        symptoms=symptoms,
        wake_at=(None if assumptions is None else cast(datetime, assumptions["wake_at"])),
        sleep_onset_at=(
            None if assumptions is None else cast(datetime, assumptions["sleep_onset_at"])
        ),
        source_model_sha256=cast(str, curve["source_revision_sha256"]),
        reference_revision=reference_revision,
    )
    if not available:
        result["missing_inputs"] = [
            f"wake_reference.{value}" for value in cast(list[str], reference["missing_inputs"])
        ]
    return result
