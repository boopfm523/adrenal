"""Synthetic performance benchmark for selectable cortisol chart models.

The benchmark is pure and rollback-free: it constructs synthetic dose objects in
memory and never opens an owner's database. The separate wearable benchmark covers
multi-year storage/query scale; this module protects the dense selected-day model
and reference-band computations that happen after those reads are bounded.
"""

from __future__ import annotations

import gc
import json
import tracemalloc
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import median
from time import perf_counter
from typing import Final

from healthcurve.analytics import exposure, physiology, wake_pharmacokinetics, wake_reference
from healthcurve.medications.models import DoseCategory, DoseUnit, Route

RESULT_SCHEMA_VERSION: Final = 1
DENSE_SELECTED_DAY_DOSE_COUNT: Final = 24
DEFAULT_RUNS: Final = 7
PEAK_MEMORY_BUDGET_MIB: Final = 16.0
LATENCY_BUDGET_MS: Final = {
    "hc-exposure-v1": 100.0,
    "hc-physiology-v2": 150.0,
    "hc-wake-free-v3": 150.0,
    "hc-wake-free-v3-with-reference": 250.0,
}


def synthetic_doses(
    *, day: date = date(2026, 1, 15), count: int = DENSE_SELECTED_DAY_DOSE_COUNT
) -> list[exposure.ExposureDose]:
    """Return a deliberately dense, deterministic selected-day fixture."""
    if not 1 <= count <= 96:
        raise ValueError("synthetic dose count must be between 1 and 96")
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    spacing = timedelta(days=1) / count
    rows = []
    for index in range(count):
        occurred_at = start + spacing * index
        rows.append(
            exposure.ExposureDose(
                id=uuid.UUID(int=index + 1),
                occurred_at=occurred_at,
                local_time=occurred_at.replace(tzinfo=None),
                timezone="UTC",
                utc_offset_minutes=0,
                amount=Decimal("2.5"),
                unit=DoseUnit.MG,
                route=Route.ORAL,
                category=(DoseCategory.STRESS if index % 5 == 4 else DoseCategory.SCHEDULED),
                medication_name="Synthetic hydrocortisone",
                normalized_medication_name="hydrocortisone",
                formulation="tablet",
                source_type="synthetic-benchmark",
                confirmation_state="synthetic",
                supersedes_id=None,
                recorded_at=occurred_at + timedelta(seconds=1),
                source_revision="synthetic-benchmark-v1",
            )
        )
    return rows


def _measure(
    name: str,
    operation: Callable[[], object],
    *,
    runs: int,
) -> dict[str, object]:
    operation()
    samples = []
    for _ in range(runs):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1_000)

    gc.collect()
    tracemalloc.start()
    operation()
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    measured_ms = round(median(samples), 3)
    peak_mib = round(peak_bytes / 1_048_576, 3)
    latency_budget_ms = LATENCY_BUDGET_MS[name]
    return {
        "name": name,
        "median_latency_ms": measured_ms,
        "latency_budget_ms": latency_budget_ms,
        "peak_memory_mib": peak_mib,
        "peak_memory_budget_mib": PEAK_MEMORY_BUDGET_MIB,
        "within_budget": measured_ms <= latency_budget_ms and peak_mib <= PEAK_MEMORY_BUDGET_MIB,
    }


def run_benchmark(*, runs: int = DEFAULT_RUNS) -> dict[str, object]:
    """Measure all selectable models against one dense synthetic day."""
    if not 1 <= runs <= 21:
        raise ValueError("runs must be between 1 and 21")
    day = date(2026, 1, 15)
    doses = synthetic_doses(day=day)

    def v1() -> object:
        return exposure.build_curve(day=day, timezone="UTC", doses=doses)

    def v2() -> object:
        return physiology.build_curve(day=day, timezone="UTC", doses=doses)

    def v3() -> object:
        return wake_pharmacokinetics.build_curve(day=day, timezone="UTC", doses=doses)

    def v3_with_reference() -> object:
        curve = wake_pharmacokinetics.build_curve(day=day, timezone="UTC", doses=doses)
        samples = curve["samples"]
        if not isinstance(samples, list):  # pragma: no cover - internal invariant
            raise TypeError("model samples must be a list")
        return wake_reference.build_reference(
            day=day,
            timezone="UTC",
            wake_at=datetime(2026, 1, 15, 6, 15, tzinfo=UTC),
            sleep_onset_at=datetime(2026, 1, 14, 23, 0, tzinfo=UTC),
            meals={
                "breakfast": datetime(2026, 1, 15, 7, 30, tzinfo=UTC),
                "lunch": datetime(2026, 1, 15, 12, 30, tzinfo=UTC),
                "dinner": datetime(2026, 1, 15, 18, 30, tzinfo=UTC),
            },
            sample_instants=[sample["occurred_at"] for sample in samples],
        )

    measurements = [
        _measure("hc-exposure-v1", v1, runs=runs),
        _measure("hc-physiology-v2", v2, runs=runs),
        _measure("hc-wake-free-v3", v3, runs=runs),
        _measure(
            "hc-wake-free-v3-with-reference",
            v3_with_reference,
            runs=runs,
        ),
    ]
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "fixture": "synthetic_in_memory_dense_selected_day",
        "dose_count": len(doses),
        "sample_interval_minutes": exposure.SAMPLE_INTERVAL_MINUTES,
        "measurements": measurements,
        "all_within_budget": all(bool(row["within_budget"]) for row in measurements),
        "multi_year_context": (
            "Multi-year database/query scale is measured separately by "
            "scripts/benchmark_wearable_scale.py; longitudinal reads use versioned daily "
            "summaries while exact chart reads remain limited to one selected day."
        ),
    }


def result_json(result: dict[str, object]) -> str:
    return json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
