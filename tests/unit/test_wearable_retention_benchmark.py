from healthcurve.wearable_benchmark import build_scale_plan
from healthcurve.wearable_retention_benchmark import result_skeleton


def test_retention_benchmark_result_is_operational_and_synthetic() -> None:
    result = result_skeleton(build_scale_plan(years=5))

    assert result["fixture"] == "synthetic_disposable_multi_year"
    assert result["expected_metric_rows"] == 3_690_540
    assert set(result) == {
        "schema_version",
        "fixture",
        "years",
        "days",
        "expected_metric_rows",
        "backup",
        "restore",
        "verification",
    }
    assert "owner" not in result
