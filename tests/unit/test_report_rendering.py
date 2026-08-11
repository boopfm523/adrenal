from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date

import pdfplumber

from healthcurve.reports.models import ReportSnapshot
from healthcurve.reports.rendering import render, render_csv, render_html, render_json
from healthcurve.reports.service import canonical_payload, checksum


def snapshot(*, include_ai: bool = False) -> ReportSnapshot:
    ai_sources = ["ai-1"] if include_ai else []
    ai_content = [{"id": "ai-1", "body": "Synthetic generated observation"}] if include_ai else []
    manifest = {
        "fact": ["fact-1"],
        "plan": ["plan-1"],
        "patient_note": ["note-1"],
        "ai": ai_sources,
    }
    content: dict[str, object] = {
        "fact": [
            {
                "id": "fact-1",
                "record_type": "dose",
                "local_time": "2026-08-09T08:00:00",
                "medication_name": "Synthetic <script>bad()</script>",
                "amount": "10.0000",
                "unit": "mg",
                "route": "oral",
                "category": "scheduled",
            }
        ],
        "plan": [{"id": "plan-1", "status": "approved"}],
        "patient_note": [{"id": "note-1", "text": "Synthetic question"}],
        "ai": ai_content,
    }
    metrics: dict[str, object] = {
        "dose_total": {
            "definition": "Sum of current recorded dose facts.",
            "timezone": "America/New_York",
            "value": "10.0000",
        }
    }
    payload = canonical_payload(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 9),
        timezone="America/New_York",
        selected_sections=["doses", "approved_plan", "patient_notes"],
        include_ai=include_ai,
        source_manifest=manifest,
        metric_values=metrics,
        snapshot_content=content,
        render_version="report-v1",
    )
    return ReportSnapshot(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 9),
        timezone="America/New_York",
        selected_sections=payload["selected_sections"],
        include_ai=include_ai,
        source_manifest=payload["source_manifest"],
        metric_values=payload["metric_values"],
        snapshot_content=payload["snapshot_content"],
        render_version="report-v1",
        canonical_sha256=checksum(payload),
    )


def test_html_json_and_csv_are_deterministic_partitioned_and_ai_off_by_default() -> None:
    report = snapshot()
    left_html = render_html(report)
    assert left_html == render_html(report)
    assert b"Recorded facts" in left_html
    assert b"Physician-approved plan" in left_html
    assert b"Patient notes and questions" in left_html
    assert b"AI-generated analysis" not in left_html
    assert b"<strong>Local timezone:</strong> EDT" in left_html
    assert b"America/New_York" not in left_html
    assert b"<script>bad()" not in left_html
    assert b"&lt;script&gt;bad()&lt;/script&gt;" in left_html

    json_bytes = render_json(report)
    assert json_bytes == render_json(report)
    assert json.loads(json_bytes)["include_ai"] is False

    csv_bytes = render_csv(report)
    assert csv_bytes == render_csv(report)
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8"))))
    assert [row["category"] for row in rows] == ["fact", "plan", "patient_note"]
    assert all(row["category"] != "ai" for row in rows)


def test_opted_in_ai_is_visually_and_structurally_separate() -> None:
    report = snapshot(include_ai=True)
    html = render_html(report)
    assert b"AI-generated analysis" in html
    assert b"Generated content" in html
    rows = list(csv.DictReader(io.StringIO(render_csv(report).decode("utf-8"))))
    assert rows[-1]["category"] == "ai"


def test_playwright_pdf_is_letter_sized_printable_and_contains_expected_text() -> None:
    bundle = render(snapshot())
    assert bundle.pdf.startswith(b"%PDF-")
    with pdfplumber.open(io.BytesIO(bundle.pdf)) as pdf:
        assert pdf.pages
        assert round(float(pdf.pages[0].width)) == 612
        assert round(float(pdf.pages[0].height)) == 792
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "HealthCurve.ai physician report" in text
    assert "Recorded facts" in text
    assert "Physician-approved plan" in text
    assert "AI-generated analysis" not in text


def test_dense_wearable_samples_are_summarized_instead_of_printed_one_per_row() -> None:
    report = snapshot()
    records = [
        {
            "id": f"garmin-{index}",
            "record_type": "garmin_metric",
            "local_time": "2026-08-09T12:00:00",
            "metric_type": "heart_rate",
            "value": str(60 + (index % 40)),
            "unit": "bpm",
        }
        for index in range(3_000)
    ]
    content = dict(report.snapshot_content)
    content["fact"] = records
    manifest = dict(report.source_manifest)
    manifest["fact"] = [record["id"] for record in records]
    selected_sections = ["wearables"]
    payload = canonical_payload(
        date_from=report.date_from,
        date_to=report.date_to,
        timezone=report.timezone,
        selected_sections=selected_sections,
        include_ai=False,
        source_manifest=manifest,
        metric_values={},
        snapshot_content=content,
        render_version=report.render_version,
    )
    report.selected_sections = selected_sections
    report.source_manifest = manifest
    report.metric_values = {}
    report.snapshot_content = content
    report.canonical_sha256 = checksum(payload)

    bundle = render(report)
    with pdfplumber.open(io.BytesIO(bundle.pdf)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert len(pdf.pages) <= 3
    assert "3,000" in text
    assert "avg 79.5; low 60; high 99" in text
    assert "garmin-2999" not in text
