"""Bounded Garmin parsing with no filesystem or database writes."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pytest

from healthcurve.integrations.garmin.parser import (
    MAX_UPLOAD_BYTES,
    ActivityCandidate,
    GarminImportError,
    MetricCandidate,
    SleepCandidate,
    parse_upload,
)
from tests.fixtures.garmin import (
    synthetic_activity_csv,
    synthetic_archive,
    synthetic_fit,
)


def test_official_sdk_fit_maps_only_explicit_metrics_and_attribution() -> None:
    payload = synthetic_fit()
    parsed = parse_upload("synthetic.fit", payload, "Europe/London")

    assert parsed.source_payload == payload
    assert parsed.source_members == ["synthetic.fit"]
    assert set(parsed.observed_metrics) == {
        "activity",
        "body_battery",
        "heart_rate",
        "hrv",
        "sleep",
        "sleep_score",
        "steps",
    }
    assert {"resting_heart_rate", "stress"} <= set(parsed.missing_metrics)
    assert parsed.device_attributions[0]["manufacturer"] == "garmin"
    assert parsed.device_attributions[0]["serial_hash"]
    assert parsed.device_attributions[0]["serial_hash"] != "42"

    metrics = [
        candidate for candidate in parsed.candidates if isinstance(candidate, MetricCandidate)
    ]
    values = {(metric.metric_type.value, metric.value, metric.unit) for metric in metrics}
    assert ("heart_rate", Decimal(75), "bpm") in values
    assert ("hrv", Decimal(47), "ms") in values
    assert ("body_battery", Decimal(64), "garmin_score") in values
    assert ("steps", Decimal(321), "steps") in values

    sleep = next(
        candidate for candidate in parsed.candidates if isinstance(candidate, SleepCandidate)
    )
    assert sleep.overall_sleep_score == 82
    assert sleep.stage_count == 2
    activity = next(
        candidate for candidate in parsed.candidates if isinstance(candidate, ActivityCandidate)
    )
    assert activity.sport == "running"
    assert activity.distance_m == Decimal("10000.0")


def test_activity_csv_requires_explicit_distance_units() -> None:
    explicit = parse_upload("activities.csv", synthetic_activity_csv(), "Europe/London")
    activity = next(
        candidate for candidate in explicit.candidates if isinstance(candidate, ActivityCandidate)
    )
    assert activity.distance_m == Decimal("25500.0")

    ambiguous = parse_upload(
        "activities.csv",
        synthetic_activity_csv(explicit_distance_unit=False),
        "Europe/London",
    )
    activity = next(
        candidate for candidate in ambiguous.candidates if isinstance(candidate, ActivityCandidate)
    )
    assert activity.distance_m is None
    assert "distance:unit_missing" in ambiguous.warnings


def test_account_archive_uses_global_safe_paths_and_preserves_members() -> None:
    parsed = parse_upload("account-export.zip", synthetic_archive(), "Europe/London")
    assert len(parsed.source_members) == 2
    assert all(name.startswith("DI_CONNECT/") for name in parsed.source_members)
    assert {"activity", "heart_rate", "sleep"} <= set(parsed.observed_metrics)


@pytest.mark.parametrize(
    ("filename", "payload", "code"),
    [
        ("empty.fit", b"", "empty_file"),
        ("notes.txt", b"not an export", "file_type_unsupported"),
        ("broken.fit", b"\x0e\x00\x00\x00\x00\x00\x00\x00.FIT", "fit_integrity_invalid"),
    ],
)
def test_malformed_inputs_fail_with_privacy_safe_codes(
    filename: str, payload: bytes, code: str
) -> None:
    with pytest.raises(GarminImportError, match=f"^{code}$"):
        parse_upload(filename, payload, "Europe/London")


def test_oversized_upload_is_rejected_before_decoding() -> None:
    with pytest.raises(GarminImportError, match=r"^file_too_large$"):
        parse_upload("huge.fit", b"x" * (MAX_UPLOAD_BYTES + 1), "UTC")


def test_archive_traversal_and_unsafe_compression_fail_closed() -> None:
    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../synthetic.csv", synthetic_activity_csv())
    with pytest.raises(GarminImportError, match=r"^archive_member_path_unsafe$"):
        parse_upload("unsafe.zip", traversal.getvalue(), "UTC")

    compressed = io.BytesIO()
    with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("synthetic.csv", b"0" * 100_000)
    with pytest.raises(GarminImportError, match=r"^archive_compression_ratio_unsafe$"):
        parse_upload("compressed.zip", compressed.getvalue(), "UTC")
