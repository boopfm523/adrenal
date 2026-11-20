"""Pure mapping rules for imported health records (ADR-0040). Synthetic data only."""

# ruff: noqa: DTZ001 -- wall-clock times are naive by design.

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from healthcurve.api.routers.health_records import resolve_dataset_file
from healthcurve.integrations.health_records.models import AiVisibilityCategory, DatasetTable
from healthcurve.integrations.health_records.service import (
    content_hash,
    project,
    resolve_wall_time,
    timezone_for,
)
from healthcurve.labs.normalization import normalize_lab_value_with_alias
from tests.fixtures.health_records_dataset import default_tables


@pytest.mark.parametrize(
    ("organization", "zone", "assumed"),
    [
        ("Mayo Clinic", "America/Chicago", False),
        ("NIH Clinical Center", "America/New_York", False),
        ("Johns Hopkins Medicine", "America/New_York", False),
        ("University of Maryland Medical System (UMMS)", "America/New_York", False),
        ("Synthetic Community Clinic", "America/New_York", True),
        ("Unknown organization", "America/New_York", True),
    ],
)
def test_owner_confirmed_time_zone_per_organization(
    organization: str, zone: str, assumed: bool
) -> None:
    assert timezone_for(organization) == (zone, assumed)


def test_repeated_fall_back_hour_takes_the_first_occurrence() -> None:
    resolved = resolve_wall_time(datetime(2025, 11, 2, 1, 30), "America/New_York")
    assert resolved.utc_offset_minutes == -240
    assert resolved.occurred_at == datetime(2025, 11, 2, 5, 30, tzinfo=UTC)


def test_skipped_spring_forward_time_keeps_its_wall_clock() -> None:
    resolved = resolve_wall_time(datetime(2025, 3, 9, 2, 30), "America/Chicago")
    assert resolved.local_time == datetime(2025, 3, 9, 2, 30)
    assert resolved.utc_offset_minutes == -360


def test_content_hash_ignores_column_order_but_not_values() -> None:
    row = default_tables()["lab_results"][0]
    reordered = dict(reversed(list(row.items())))
    assert content_hash(row) == content_hash(reordered)
    changed = {**row, "value_text": "14.0"}
    assert content_hash(changed) != content_hash(row)


def test_medication_lists_have_no_ai_category() -> None:
    row = default_tables()["medications"][0]
    projection = project(DatasetTable.MEDICATIONS, row)
    assert projection.ai_category is None
    assert projection.summary_text == "Take one tablet daily"
    assert projection.prescriber == "Dr. Synthetic Prescriber"


def test_documents_fall_under_their_folder_category_and_avs_wins() -> None:
    note, avs = default_tables()["documents"]
    assert project(DatasetTable.DOCUMENTS, note).ai_category is (
        AiVisibilityCategory.CLINICAL_NOTES
    )
    assert project(DatasetTable.DOCUMENTS, avs).ai_category is (
        AiVisibilityCategory.VISIT_SUMMARIES
    )
    unknown = {**note, "category": "Health-Summary"}
    assert project(DatasetTable.DOCUMENTS, unknown).ai_category is (
        AiVisibilityCategory.OTHER_DOCUMENTS
    )


def test_free_text_dates_are_kept_and_iso_dates_parsed() -> None:
    condition = project(DatasetTable.CONDITIONS, default_tables()["conditions"][0])
    assert condition.record_date is None
    assert condition.date_text == "sept '24"
    encounter = project(DatasetTable.ENCOUNTERS, default_tables()["encounters"][0])
    assert encounter.record_date == date(2025, 2, 1)
    assert encounter.local_time == datetime(2025, 2, 1, 9, 30)
    assert len(encounter.file_paths) == 2


def test_imaging_keeps_impression_report_and_clinicians_apart() -> None:
    study = project(DatasetTable.IMAGING_STUDIES, default_tables()["imaging_studies"][0])
    assert study.summary_text is not None and study.summary_text.startswith("Synthetic impr")
    assert study.body_text == "Synthetic report text."
    assert study.reading_provider == "Dr. Synthetic Radiologist"
    assert study.ai_category is AiVisibilityCategory.IMAGING_IMPRESSIONS


def test_specimen_suffixed_alias_is_never_normalized_as_blood() -> None:
    assert normalize_lab_value_with_alias("Glucose", "Glucose (urine)", "100", "mg/dL") is None


def test_dataset_alias_is_tried_when_the_lab_name_is_unknown() -> None:
    normalized = normalize_lab_value_with_alias("Cortisol, Serum", "Cortisol", "12.0", "mcg/dL")
    assert normalized is not None
    assert normalized.analyte_code == "cortisol"
    assert normalized.value == Decimal("331.2000000000")


def test_owner_entered_names_with_parentheses_still_normalize() -> None:
    normalized = normalize_lab_value_with_alias("Neutrophils (%)", None, "55", "%")
    assert normalized is not None and normalized.analyte_code == "neutrophils_percent"


def test_dataset_files_resolve_only_inside_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "records").mkdir(parents=True)
    (root / "records" / "a.pdf").write_bytes(b"%PDF-synthetic")
    (root / "records" / "a.exe").write_bytes(b"x")
    (tmp_path / "secret.txt").write_text("outside")
    (root / "records" / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert resolve_dataset_file(root, "records/a.pdf") == (root / "records" / "a.pdf").resolve()
    assert resolve_dataset_file(root, "../secret.txt") is None
    assert resolve_dataset_file(root, "/etc/hosts") is None
    assert resolve_dataset_file(root, "records/link.txt") is None
    assert resolve_dataset_file(root, "records/a.exe") is None
    assert resolve_dataset_file(root, "records/missing.pdf") is None
