"""SAFE-29: fixtures are synthetic only, and stay that way.

The generator is deterministic and marks everything it produces. These tests check the
properties that make the marker meaningful, and scan the repository for the shapes real
personal data would take if someone pasted it in.
"""

from __future__ import annotations

import re
from datetime import UTC
from decimal import Decimal
from pathlib import Path

import pytest

from tests.fixtures.synthetic import SYNTHETIC_MARKER, generate_record

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_generation_is_deterministic() -> None:
    assert generate_record(seed=7) == generate_record(seed=7)


def test_different_seeds_give_different_records() -> None:
    assert generate_record(seed=1) != generate_record(seed=2)


@pytest.mark.safety("SAFE-29")
def test_every_generated_record_is_marked_synthetic() -> None:
    record = generate_record(seed=3)
    assert record.marker == SYNTHETIC_MARKER
    for dose in record.doses:
        assert dose.marker == SYNTHETIC_MARKER
    for symptom in record.symptoms:
        assert symptom.marker == SYNTHETIC_MARKER
    for episode in record.episodes:
        assert episode.marker == SYNTHETIC_MARKER
        assert all(d.marker == SYNTHETIC_MARKER for d in episode.doses)


def test_amounts_are_decimal_not_float() -> None:
    """ADR-0001: binary floats are prohibited for clinical quantities."""
    for dose in generate_record(seed=4).doses:
        assert isinstance(dose.amount, Decimal)
        assert not isinstance(dose.amount, float)


def test_all_timestamps_are_timezone_aware() -> None:
    """SAFE-09: a naive datetime in a medication record is a clinical bug."""
    record = generate_record(seed=5)
    for dose in record.doses:
        assert dose.occurred_at_utc.tzinfo is not None
        assert dose.occurred_at_utc.tzinfo == UTC
        assert dose.local_time.tzinfo is not None
    for symptom in record.symptoms:
        assert symptom.occurred_at_utc.tzinfo is not None


def test_fixture_spans_a_dst_transition() -> None:
    """The default window must exercise offset changes, not sit comfortably in UTC."""
    offsets = {dose.utc_offset_minutes for dose in generate_record(seed=6).doses}
    assert len(offsets) > 1, "default synthetic record should cross a DST boundary"


@pytest.mark.safety("SAFE-10")
def test_skipped_doses_produce_no_record_rather_than_a_zero() -> None:
    """Missing is not zero -- a skipped dose is an absence, never a 0 mg row."""
    record = generate_record(seed=8, days=60)
    assert record.doses, "sanity: the generator produced doses"
    assert all(dose.amount > 0 for dose in record.doses)


# ---------------------------------------------------------------------------
# Repository scan
# ---------------------------------------------------------------------------

_SCANNED_SUFFIXES = {".py", ".json", ".csv", ".yaml", ".yml", ".md", ".sql", ".env"}
_SKIP_DIRS = {".git", ".venv", "node_modules", ".beads", ".ruff_cache", ".pytest_cache", "dist"}

# Shapes that real personal data takes. Deliberately narrow to avoid false positives:
# a full national-insurance-style identifier, an NHS-style number, an IBAN-ish string.
_REAL_DATA_PATTERNS = (
    re.compile(r"\b\d{3}[- ]\d{3}[- ]\d{4}\b"),  # NHS-style number
    re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b"),  # UK NI number
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US SSN
)


def _scannable_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        files.append(path)
    return files


@pytest.mark.safety("SAFE-29")
def test_repository_contains_no_personal_identifier_shapes() -> None:
    offenders: list[str] = []
    for path in _scannable_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in _REAL_DATA_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(REPO_ROOT)} matched {pattern.pattern}")
    assert not offenders, f"possible real personal data in repository: {offenders}"


@pytest.mark.safety("SAFE-29")
def test_env_file_is_not_committed() -> None:
    """A committed .env is the most common way credentials leak (T3)."""
    assert not (REPO_ROOT / ".env").exists() or ".env" in (REPO_ROOT / ".gitignore").read_text(
        encoding="utf-8"
    ), ".env exists and is not git-ignored"
