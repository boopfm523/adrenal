from __future__ import annotations

from time import sleep
from typing import cast

from pytest import MonkeyPatch

from healthcurve import cortisol_benchmark
from healthcurve.cortisol_benchmark import run_benchmark, synthetic_doses


def test_dense_selected_day_fixture_is_synthetic_and_bounded() -> None:
    rows = synthetic_doses()

    assert len(rows) == 24
    assert all(row.source_type == "synthetic-benchmark" for row in rows)
    assert len({row.id for row in rows}) == len(rows)


def test_selectable_cortisol_models_stay_within_dense_day_budgets() -> None:
    result = run_benchmark(runs=3)

    assert result["fixture"] == "synthetic_in_memory_dense_selected_day"
    assert result["dose_count"] == 24
    measurements = cast(list[dict[str, object]], result["measurements"])
    assert result["all_within_budget"] is True, measurements
    assert {row["name"] for row in measurements} == {
        "hc-exposure-v1",
        "hc-physiology-v2",
        "hc-wake-free-v3",
        "hc-wake-free-v3-with-reference",
    }


def test_model_latency_budget_ignores_shared_runner_waits(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setitem(cortisol_benchmark.LATENCY_BUDGET_MS, "hc-exposure-v1", 1.0)

    measurement = cortisol_benchmark._measure(  # pyright: ignore[reportPrivateUsage]
        "hc-exposure-v1",
        lambda: sleep(0.01),
        runs=3,
    )

    assert cast(float, measurement["median_latency_ms"]) <= 1.0
    assert measurement["within_budget"] is True
