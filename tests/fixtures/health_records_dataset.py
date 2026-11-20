"""A small synthetic health-records dataset built to the DATASET.md v2 schema (ADR-0040).

Every value is invented. Clinician names say "Synthetic", record IDs are made-up
hashes, and no row comes from a real record. Organization names are real institutions
only because the importer's per-organization time zones key on them.
"""

from __future__ import annotations

import copy
import sqlite3
from pathlib import Path
from typing import Any

SYNTHETIC_MARKER = "synthetic-health-records-fixture"

SCHEMA = """
CREATE TABLE documents (
  source TEXT, category TEXT, kind TEXT, title TEXT, date TEXT, provider TEXT,
  file_path TEXT, file_type TEXT, original_id TEXT, text TEXT, organization TEXT, record_id TEXT);
CREATE TABLE imaging_studies (
  source TEXT, date TEXT, exam_name TEXT, exam_normalized TEXT, modality TEXT,
  accession TEXT, ordered_by TEXT, report_text TEXT, report_file TEXT, pdf_file TEXT,
  organization TEXT, reading_provider TEXT, impression TEXT, is_outside_import INTEGER,
  record_id TEXT);
CREATE TABLE lab_results (
  source TEXT, date TEXT, datetime TEXT, panel TEXT, test TEXT, value_text TEXT, value_num REAL,
  units TEXT, ref_range TEXT, ref_low REAL, ref_high REAL, flag TEXT, comment TEXT,
  file_path TEXT, organization TEXT, test_code TEXT, test_normalized TEXT, ordered_by TEXT,
  performing_lab TEXT, specimen TEXT, record_id TEXT);
CREATE TABLE vitals (
  source TEXT, date TEXT, datetime TEXT, name TEXT, value_text TEXT, value_num REAL,
  units TEXT, organization TEXT, record_id TEXT);
CREATE TABLE conditions (
  source TEXT, name TEXT, status TEXT, onset_date TEXT, resolved_date TEXT, icd_code TEXT,
  snomed TEXT, organization TEXT, record_id TEXT);
CREATE TABLE immunizations (
  source TEXT, name TEXT, date TEXT, status TEXT, organization TEXT, record_id TEXT);
CREATE TABLE allergies (
  source TEXT, name TEXT, status TEXT, reactions TEXT, organization TEXT, record_id TEXT);
CREATE TABLE encounters (
  source TEXT, organization TEXT, date TEXT, time TEXT, visit_type TEXT, department TEXT,
  specialty TEXT, provider TEXT, status TEXT, note_count INTEGER, note_files TEXT,
  avs_file TEXT, record_id TEXT);
CREATE TABLE medications (
  source TEXT, organization TEXT, name TEXT, sig TEXT, start_date TEXT, date_noted TEXT,
  prescriber TEXT, patient_reported INTEGER, record_id TEXT);
CREATE TABLE surgical_history (
  source TEXT, organization TEXT, name TEXT, date_text TEXT, record_id TEXT);
CREATE TABLE dataset_meta (key TEXT PRIMARY KEY, value TEXT);
"""

NIH = ("NIH Clinical Center", "NIH Clinical Center")
HOPKINS = ("Johns Hopkins MyChart", "Johns Hopkins Medicine")
MAYO = ("Johns Hopkins MyChart", "Mayo Clinic")
UMMS = ("Johns Hopkins MyChart", "University of Maryland Medical System (UMMS)")
INBOX = ("Manual upload (Inbox)", "Synthetic Community Clinic")


def _lab(
    record_id: str,
    origin: tuple[str, str],
    when: str,
    panel: str,
    test: str,
    value: str | None,
    units: str | None,
    *,
    normalized: str | None = None,
    specimen: str | None = None,
    flag: str | None = None,
) -> dict[str, Any]:
    number: float | None
    try:
        number = float(value) if value is not None else None
    except ValueError:
        number = None
    return {
        "source": origin[0],
        "organization": origin[1],
        "date": when[:10],
        "datetime": when,
        "panel": panel,
        "test": test,
        "value_text": value,
        "value_num": number,
        "units": units,
        "ref_range": "1.0-9.0" if number is not None else None,
        "ref_low": 1.0 if number is not None else None,
        "ref_high": 9.0 if number is not None else None,
        "flag": flag,
        "comment": None,
        "file_path": f"records/{origin[1].split()[0]}/Labs/{record_id}.md",
        "test_code": test.upper(),
        "test_normalized": normalized or test,
        "ordered_by": "Dr. Synthetic Orderer",
        "performing_lab": None,
        "specimen": specimen,
        "record_id": record_id,
    }


def default_tables() -> dict[str, list[dict[str, Any]]]:
    """A fresh copy of the default synthetic rows, safe to mutate per test."""
    tables: dict[str, list[dict[str, Any]]] = {
        "lab_results": [
            _lab(
                "lab_syn_001",
                NIH,
                "2025-01-15 08:05",
                "CBC",
                "HGB",
                "13.5",
                "g/dL",
                normalized="Hemoglobin",
            ),
            _lab(
                "lab_syn_002",
                NIH,
                "2025-01-15 08:05",
                "CBC",
                "WBC",
                "6.1",
                "K/uL",
                normalized="WBC",
            ),
            _lab(
                "lab_syn_003",
                HOPKINS,
                "2025-02-01 07:30",
                "BASIC METABOLIC PANEL",
                "Sodium",
                "139",
                "mmol/L",
                specimen="Blood",
            ),
            _lab(
                "lab_syn_004",
                HOPKINS,
                "2025-02-01 07:30",
                "URINALYSIS",
                "Glucose",
                "Negative",
                None,
                normalized="Glucose (urine)",
                specimen="Urine",
            ),
            _lab(
                "lab_syn_005",
                MAYO,
                "2025-03-10 09:00",
                "CORTISOL AM",
                "Cortisol, Serum",
                "12.0",
                "mcg/dL",
                normalized="Cortisol",
            ),
            _lab(
                "lab_syn_006",
                UMMS,
                "2025-04-02 10:15",
                "ACTH",
                "ACTH",
                "<5",
                "pg/mL",
                normalized="ACTH",
                flag="Low",
            ),
            # A source row with no result at all is kept as a record only.
            _lab(
                "lab_syn_007",
                HOPKINS,
                "2025-02-01 07:30",
                "BASIC METABOLIC PANEL",
                "Comment",
                None,
                None,
            ),
        ],
        "imaging_studies": [
            {
                "source": HOPKINS[0],
                "organization": HOPKINS[1],
                "date": "2025-05-05",
                "exam_name": "CT CHEST WITH CONTRAST",
                "exam_normalized": "ct chest with contrast",
                "modality": "CT",
                "accession": "SYN-ACC-1",
                "ordered_by": "Dr. Synthetic Orderer",
                "report_text": "Synthetic report text.",
                "report_file": "records/Hopkins/Radiology/img_syn_001.md",
                "pdf_file": None,
                "reading_provider": "Dr. Synthetic Radiologist",
                "impression": "Synthetic impression: no acute finding.",
                "is_outside_import": 0,
                "record_id": "img_syn_001",
            }
        ],
        "documents": [
            {
                "source": NIH[0],
                "organization": NIH[1],
                "category": "Clinical-Notes",
                "kind": "Note",
                "title": "Synthetic progress note",
                "date": "2025-01-20",
                "provider": "Dr. Synthetic Author",
                "file_path": "records/NIH/Clinical-Notes/doc_syn_001.txt",
                "file_type": "txt",
                "original_id": "SYN-1",
                "text": "Organization: NIH Clinical Center\nSynthetic note body.",
                "record_id": "doc_syn_001",
            },
            {
                "source": HOPKINS[0],
                "organization": HOPKINS[1],
                "category": "Visit-Summaries",
                "kind": "AVS",
                "title": "Synthetic after visit summary",
                "date": "2025-02-01",
                "provider": None,
                "file_path": "records/Hopkins/Visit-Summaries/doc_syn_002.html",
                "file_type": "html",
                "original_id": "SYN-2",
                "text": "Synthetic AVS body.",
                "record_id": "doc_syn_002",
            },
        ],
        "encounters": [
            {
                "source": HOPKINS[0],
                "organization": HOPKINS[1],
                "date": "2025-02-01",
                "time": "09:30",
                "visit_type": "Office Visit",
                "department": "Synthetic Endocrinology",
                "specialty": "Endocrinology",
                "provider": "Dr. Synthetic Clinician",
                "status": "Completed",
                "note_count": 1,
                "note_files": "records/Hopkins/Clinical-Notes/a.md; records/Hopkins/b.md",
                "avs_file": None,
                "record_id": "enc_syn_001",
            }
        ],
        "medications": [
            {
                "source": HOPKINS[0],
                "organization": HOPKINS[1],
                "name": "Synthetic medication 5 mg tablet",
                "sig": "Take one tablet daily",
                "start_date": "2024-06-01",
                "date_noted": None,
                "prescriber": "Dr. Synthetic Prescriber",
                "patient_reported": 0,
                "record_id": "med_syn_001",
            }
        ],
        "conditions": [
            {
                "source": NIH[0],
                "organization": NIH[1],
                "name": "Synthetic condition",
                "status": "Active",
                "onset_date": "sept '24",
                "resolved_date": None,
                "icd_code": None,
                "snomed": None,
                "record_id": "cond_syn_001",
            }
        ],
        "immunizations": [
            {
                "source": NIH[0],
                "organization": NIH[1],
                "name": "Synthetic vaccine",
                "date": "2024-10-01",
                "status": "Given",
                "record_id": "imm_syn_001",
            }
        ],
        "allergies": [
            {
                "source": HOPKINS[0],
                "organization": HOPKINS[1],
                "name": "Synthetic allergen",
                "status": "Active",
                "reactions": "Synthetic rash",
                "record_id": "alg_syn_001",
            }
        ],
        "vitals": [
            {
                "source": NIH[0],
                "organization": NIH[1],
                "date": "2025-01-15",
                "datetime": "2025-01-15 08:00",
                "name": "Weight",
                "value_text": "70",
                "value_num": 70.0,
                "units": "kg",
                "record_id": "vit_syn_001",
            }
        ],
        "surgical_history": [
            {
                "source": INBOX[0],
                "organization": INBOX[1],
                "name": "Synthetic procedure",
                "date_text": "2023",
                "record_id": "surg_syn_001",
            }
        ],
    }
    return copy.deepcopy(tables)


def default_meta() -> dict[str, str]:
    return {
        "schema_version": "2",
        "generated_at": "2025-06-01T12:00:00",
        "exported_at:NIH Clinical Center": "2025-05-30",
        "exported_at:Johns Hopkins MyChart": "2025-06-01",
    }


def write_dataset(
    root: Path,
    *,
    tables: dict[str, list[dict[str, Any]]] | None = None,
    meta: dict[str, str] | None = None,
    schema: str = SCHEMA,
) -> Path:
    """Write ``root/data/health.db`` (replacing any previous one) and return ``root``."""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    path = data / "health.db"
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(schema)
        for table, rows in (tables if tables is not None else default_tables()).items():
            for row in rows:
                columns = ", ".join(row)
                marks = ", ".join("?" for _ in row)
                connection.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({marks})",
                    tuple(row.values()),
                )
        for key, value in (meta if meta is not None else default_meta()).items():
            connection.execute("INSERT INTO dataset_meta (key, value) VALUES (?, ?)", (key, value))
        connection.commit()
    finally:
        connection.close()
    return root
