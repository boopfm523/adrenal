from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest

from healthcurve.wearable_benchmark import (
    RESULT_SCHEMA_VERSION,
    BenchmarkSafetyError,
    build_scale_plan,
    require_empty_database,
    result_skeleton,
)


def test_scale_plan_counts_native_cadence_and_leap_days() -> None:
    scale = build_scale_plan(years=5, start_date=date(2020, 1, 1))

    assert scale.days == 1_827
    assert scale.provider_samples_per_day == 2_016
    assert scale.daily_aggregates_per_day == 4
    assert scale.provider_sample_rows == 3_683_232
    assert scale.daily_aggregate_rows == 7_308
    assert scale.total_metric_rows == 3_690_540


def test_scale_plan_requires_a_multi_year_bounded_fixture() -> None:
    with pytest.raises(ValueError, match="between 2 and 10"):
        build_scale_plan(years=1)
    with pytest.raises(ValueError, match="between 2 and 10"):
        build_scale_plan(years=11)


def test_result_skeleton_has_versioned_machine_readable_sections() -> None:
    result = result_skeleton(build_scale_plan(years=2))

    assert result["schema_version"] == RESULT_SCHEMA_VERSION
    assert result["fixture"] == "synthetic_rollback_only"
    assert set(result) == {
        "schema_version",
        "fixture",
        "scale",
        "actual_rows",
        "measurements",
        "query_plans",
        "storage",
        "findings",
    }
    scale = result["scale"]
    assert isinstance(scale, dict)
    assert scale["start_date"] == "2020-01-01"
    assert scale["end_date_exclusive"] == "2022-01-01"


def test_benchmark_refuses_a_database_containing_any_owner() -> None:
    connection = Mock()
    connection.scalar.return_value = 1

    with pytest.raises(BenchmarkSafetyError, match="empty, disposable migrated database"):
        require_empty_database(connection)


def test_benchmark_accepts_an_empty_disposable_database() -> None:
    connection = Mock()
    connection.scalar.return_value = 0

    require_empty_database(connection)
