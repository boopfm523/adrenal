"""Measured-scale coverage for the compact private-model day projection."""

from __future__ import annotations

import json
from typing import Any, cast

from healthcurve.analytics.day_analysis import build_model_inputs


def test_full_day_model_input_is_lossless_and_bounded_at_measured_scale() -> None:
    garmin_rows = [
        {
            "metric_type": ("heart_rate", "stress", "hrv", "respiration")[index % 4],
            "unit": ("bpm", "score", "ms", "breaths/min")[index % 4],
            "bucket_start_local": f"2026-08-11T{index // 8:02d}:{(index % 4) * 15:02d}:00-04:00",
            "sample_count": 5 + index % 3,
            "minimum": f"{10 + index % 20}.0000",
            "average": f"{11 + index % 20}.5000",
            "maximum": f"{13 + index % 20}.0000",
            "bucket_local_hour": index // 8,
            "bucket_local_minute": (index % 4) * 15,
        }
        for index in range(192)
    ]
    exposure_rows = [
        {
            "bucket_start_local": f"2026-08-11T{index // 4:02d}:{(index % 4) * 15:02d}:00-04:00",
            "sample_count": 3,
            "minimum_reu": f"{index / 100:.4f}",
            "average_reu": f"{index / 100 + 0.01:.4f}",
            "maximum_reu": f"{index / 100 + 0.02:.4f}",
            "bucket_local_hour": index // 4,
            "bucket_local_minute": (index % 4) * 15,
        }
        for index in range(97)
    ]
    facts: dict[str, object] = {
        "doses": [{"medication": "Synthetic medicine", "amount": "10.0000"}],
        "symptoms": [{"name": "Synthetic symptom", "severity": 4}],
        "episodes": [{"trigger": "Synthetic stressor", "severity": "mild"}],
        "blood_pressure": [{"systolic": 120, "diastolic": 80}],
        "diary_and_life_context": [{"summary": "SYNTHETIC_ONLY"}],
        "labs": [{"test": "Synthetic lab", "value": "1.0000"}],
        "physician_approved_plans": [{"version": "Synthetic plan"}],
        "garmin_intraday_15_minute_buckets": garmin_rows,
    }
    projection: dict[str, object] = {
        "projection_version": "hc-day-analysis-v1",
        "selected_local_date": "2026-08-11",
        "selected_timezone": "America/New_York",
        "data_availability_counts": {key: 1 for key in facts if not key.startswith("garmin")},
        "missing_domains": [],
        "recorded_facts_and_plan_context": facts,
        "theoretical_exposure_15_minute_buckets": exposure_rows,
        "source_revision_sha256": "a" * 64,
        "source_record_id": f"healthcurve-day:2026-08-11:{'a' * 64}",
        "source_record_ids": [f"synthetic-source-{index:04d}" for index in range(1_138)],
    }

    compact = build_model_inputs(projection)
    compact_facts = cast(dict[str, Any], compact["recorded_facts_and_plan_context"])
    compact_garmin = cast(dict[str, Any], compact_facts["garmin_intraday_15_minute_buckets"])
    compact_exposure = cast(dict[str, Any], compact["theoretical_exposure_15_minute_buckets"])

    assert "source_record_ids" not in compact
    assert set(compact_facts) == set(facts)
    assert len(compact_garmin["rows"]) == 192
    assert len(compact_exposure["rows"]) == 97
    assert sum(cast(int, row[3]) for row in compact_garmin["rows"]) == sum(
        cast(int, row["sample_count"]) for row in garmin_rows
    )
    assert sum(cast(int, row[1]) for row in compact_exposure["rows"]) == sum(
        cast(int, row["sample_count"]) for row in exposure_rows
    )

    compact_size = len(json.dumps(compact, separators=(",", ":")))
    verbose_size = len(
        json.dumps(
            {key: value for key, value in projection.items() if key != "source_record_ids"},
            separators=(",", ":"),
        )
    )
    assert compact_size < 45_000
    assert compact_size < verbose_size * 0.7
