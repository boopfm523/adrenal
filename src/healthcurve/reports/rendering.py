"""Deterministic local rendering of immutable physician-report snapshots."""

# The embedded, audited print template is intentionally kept in this module so the
# packaged runtime cannot omit it. CSS and Jinja lines are clearer intact.
# ruff: noqa: E501

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from jinja2 import Environment, StrictUndefined, select_autoescape
from playwright.sync_api import sync_playwright

from healthcurve.events.timekeeping import timezone_abbreviation_for_local_date
from healthcurve.reports.models import ReportSnapshot
from healthcurve.reports.presentation import presentation
from healthcurve.reports.service import PARTITIONS, document

_CATEGORY_LABELS: Final = {
    "fact": "Recorded facts",
    "plan": "Physician-approved plan",
    "patient_note": "Patient notes and questions",
    "ai": "AI-generated analysis",
}

_TEMPLATE: Final = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HealthCurve physician report</title>
<style>
  @page { size: Letter; margin: 0.55in 0.55in 0.65in; }
  * { box-sizing: border-box; }
  body { margin: 0; color: #17211d; font: 9.5pt/1.35 Arial, sans-serif; }
  h1 { margin: 0 0 0.08in; color: #153f33; font-size: 22pt; letter-spacing: -0.4pt; }
  h2 { margin: 0.25in 0 0.08in; border-bottom: 2px solid #365f50; padding-bottom: 0.04in; color: #153f33; font-size: 14pt; break-after: avoid; }
  h3 { margin: 0 0 0.04in; font-size: 10.5pt; break-after: avoid-page; }
  p { margin: 0.04in 0; }
  .subtitle { color: #48554f; font-size: 11pt; }
  .boundary { margin-top: 0.12in; border-left: 4px solid #a3361f; padding: 0.08in 0.12in; background: #fff7ea; }
  .summary-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.08in; margin-top: 0.16in; }
  .summary-card { border: 1px solid #cbd4ce; border-radius: 0.05in; padding: 0.09in; background: #f8faf8; break-inside: avoid; }
  .summary-card strong { display: block; color: #153f33; font-size: 16pt; }
  .metric-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.08in; }
  .metric-card { border-left: 3px solid #55708a; padding: 0.07in 0.1in; background: #f5f9fc; break-inside: avoid; }
  .metric-card ul { margin: 0.03in 0 0; padding-left: 0.18in; }
  .section-note { color: #48554f; }
  .plan-boundary { border-left: 4px solid #29437a; padding-left: 0.1in; }
  .ai-boundary { border: 2px dashed #684a76; padding: 0.12in; background: #f7f1fa; }
  table { width: 100%; border-collapse: collapse; table-layout: auto; break-before: avoid-page; }
  thead { display: table-header-group; }
  tr { break-inside: avoid; }
  th, td { border-bottom: 1px solid #cbd4ce; padding: 0.045in; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
  thead th { background: #e8eeea; color: #243a32; font-size: 8.5pt; }
  tbody th, tbody td { font-size: 8.5pt; }
  .empty { padding: 0.09in; color: #59645f; background: #f7f8f7; }
  .provenance { margin-top: 0.3in; color: #48554f; font-size: 8pt; break-before: auto; }
  .checksum { overflow-wrap: anywhere; font-family: ui-monospace, monospace; }
</style>
</head>
<body>
<header>
  <h1>HealthCurve physician report</h1>
  <p class="subtitle">Recorded medication use and health context for clinical review</p>
  <p><strong>Period:</strong> {{ date_from }} through {{ date_to }} &nbsp; <strong>Local timezone:</strong> {{ timezone }}</p>
  <p class="boundary"><strong>Scope and safety:</strong> This report organizes recorded facts and deterministic summaries. It does not measure cortisol, diagnose a condition, establish causation, or recommend medication changes. Missing observations remain missing, never zero.</p>
</header>
<section aria-labelledby="overview-heading">
  <h2 id="overview-heading">At a glance</h2>
  <div class="summary-grid">{% for item in view.summary %}<div class="summary-card"><strong>{{ item.value }}</strong>{{ item.label }}</div>{% endfor %}</div>
</section>
{% if view.metric_overview %}
<section aria-labelledby="patterns-heading">
  <h2 id="patterns-heading">Period overview</h2>
  <p class="section-note">Counts and comparisons below are deterministic descriptions of the selected records.</p>
  <div class="metric-grid">{% for metric in view.metric_overview %}<article class="metric-card"><h3>{{ metric.title }}</h3><ul>{% for line in metric.lines %}<li>{{ line }}</li>{% endfor %}</ul></article>{% endfor %}</div>
</section>
{% endif %}
{% if "approved_plan" in selected_section_keys %}
<section class="plan-boundary" aria-labelledby="plan-heading">
  <h2 id="plan-heading">Physician-approved plan</h2>
  <p class="section-note"><strong>Approved plan only.</strong> Kept separate from actual recorded doses.</p>
  {% for table in [view.plan_table, view.instruction_table] %}<h3>{{ table.title }}</h3>{% if table.rows %}<table><thead><tr>{% for header in table.headers %}<th scope="col">{{ header }}</th>{% endfor %}</tr></thead><tbody>{% for row in table.rows %}<tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>{% else %}<p class="empty">{{ table.empty }}</p>{% endif %}{% endfor %}
</section>
{% endif %}
<section aria-labelledby="facts-heading">
  <h2 id="facts-heading">Recorded facts</h2>
  {% for table in view.tables %}<section aria-label="{{ table.title }}"><h3>{{ table.title }}</h3>{% if table.rows %}<table><thead><tr>{% for header in table.headers %}<th scope="col">{{ header }}</th>{% endfor %}</tr></thead><tbody>{% for row in table.rows %}<tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>{% else %}<p class="empty">{{ table.empty }}</p>{% endif %}</section>{% endfor %}
</section>
{% if "patient_notes" in selected_section_keys %}
<section aria-labelledby="notes-heading"><h2 id="notes-heading">Patient notes and questions</h2>{% set table = view.note_table %}{% if table.rows %}<table><thead><tr>{% for header in table.headers %}<th scope="col">{{ header }}</th>{% endfor %}</tr></thead><tbody>{% for row in table.rows %}<tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>{% else %}<p class="empty">{{ table.empty }}</p>{% endif %}</section>
{% endif %}
{% if include_ai %}
<section class="ai-boundary" aria-labelledby="ai-heading"><h2 id="ai-heading">AI-generated analysis</h2><p><strong>Generated content - not a recorded fact or physician-approved instruction.</strong> Review against source records.</p>{% set table = view.ai_table %}{% if table.rows %}<table><thead><tr>{% for header in table.headers %}<th scope="col">{{ header }}</th>{% endfor %}</tr></thead><tbody>{% for row in table.rows %}<tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>{% else %}<p class="empty">{{ table.empty }}</p>{% endif %}</section>
{% endif %}
<footer class="provenance">
  <h2>Report provenance</h2>
  <p>This immutable snapshot contains {{ view.partition_counts.fact }} recorded fact(s), {{ view.partition_counts.plan }} approved plan version(s), {{ view.partition_counts.patient_note }} patient note(s), and {{ view.partition_counts.ai }} AI analysis item(s).</p>
  <p>Render version: {{ render_version }}. Selected sections: {{ selected_sections }}.</p>
  <p class="checksum"><strong>Snapshot checksum:</strong> {{ checksum }}</p>
  <p>Exact source identifiers and machine-readable frozen data remain available in the optional JSON/CSV audit companions rather than cluttering this clinical view.</p>
</footer>
</body>
</html>"""

_ENVIRONMENT: Final = Environment(
    autoescape=select_autoescape(default=True), undefined=StrictUndefined
)
_HTML = _ENVIRONMENT.from_string(_TEMPLATE)


@dataclass(frozen=True, slots=True)
class RenderedReport:
    html: bytes
    pdf: bytes
    csv: bytes
    json: bytes


def _canonical_json(value: Any, *, pretty: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )


def render_html(snapshot: ReportSnapshot) -> bytes:
    payload = document(snapshot)
    display_day = date.fromisoformat(payload["date_to"])
    html = _HTML.render(
        date_from=payload["date_from"],
        date_to=payload["date_to"],
        timezone=timezone_abbreviation_for_local_date(payload["timezone"], display_day),
        checksum=snapshot.canonical_sha256,
        view=presentation(payload),
        include_ai=payload["include_ai"],
        render_version=payload["render_version"],
        selected_section_keys=payload["selected_sections"],
        selected_sections=", ".join(payload["selected_sections"]),
    )
    return html.encode("utf-8")


def render_json(snapshot: ReportSnapshot) -> bytes:
    return (_canonical_json(document(snapshot), pretty=True) + "\n").encode("utf-8")


def render_csv(snapshot: ReportSnapshot) -> bytes:
    payload = document(snapshot)
    content = payload["snapshot_content"]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("category", "source_record_id", "record_json"))
    manifest = payload["source_manifest"]
    for category in PARTITIONS:
        if category == "ai" and not payload["include_ai"]:
            continue
        records = content[category]
        source_ids = manifest[category]
        for index, record in enumerate(records):
            source_id = source_ids[index] if index < len(source_ids) else ""
            writer.writerow((category, source_id, _canonical_json(record)))
    return output.getvalue().encode("utf-8")


def render_pdf(html: bytes) -> bytes:
    """Print already-escaped HTML with a local, network-blocked Chromium process."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content(html.decode("utf-8"), wait_until="load")
            page.emulate_media(media="print")
            return page.pdf(
                format="Letter",
                print_background=True,
                prefer_css_page_size=True,
                tagged=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="width:100%;font:8px Arial;color:#59645f;padding:0 0.55in;display:flex;justify-content:space-between">'
                    '<span>HealthCurve - private health record</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>'
                ),
            )
        finally:
            browser.close()


def render(snapshot: ReportSnapshot) -> RenderedReport:
    html = render_html(snapshot)
    return RenderedReport(
        html=html,
        pdf=render_pdf(html),
        csv=render_csv(snapshot),
        json=render_json(snapshot),
    )
