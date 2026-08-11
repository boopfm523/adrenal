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
  @page { size: Letter; margin: 0.55in; }
  * { box-sizing: border-box; }
  body { color: #17211d; font: 10.5pt/1.4 Arial, sans-serif; }
  h1 { margin: 0 0 0.15in; font-size: 20pt; }
  h2 { margin: 0.25in 0 0.08in; border-bottom: 2px solid #365f50; padding-bottom: 0.04in; font-size: 14pt; break-after: avoid; }
  h3 { margin-bottom: 0.04in; font-size: 11pt; }
  p { margin: 0.05in 0; }
  .boundary { border-left: 4px solid #29437a; padding: 0.08in 0.12in; background: #f5f7fc; }
  .ai-boundary { border: 2px dashed #684a76; padding: 0.12in; background: #f7f1fa; }
  .patient-boundary { border-left: 4px solid #a86712; padding-left: 0.12in; }
  .record { margin: 0.08in 0; border: 1px solid #cbd4ce; padding: 0.08in; break-inside: avoid; }
  pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 8.5pt/1.35 ui-monospace, monospace; }
  table { width: 100%; border-collapse: collapse; }
  th, td { border-bottom: 1px solid #cbd4ce; padding: 0.05in; text-align: left; vertical-align: top; }
  .provenance { margin-top: 0.3in; color: #48554f; font-size: 8.5pt; }
</style>
</head>
<body>
<header>
  <h1>HealthCurve physician report</h1>
  <p><strong>Reporting period:</strong> {{ date_from }} through {{ date_to }}</p>
  <p><strong>Timezone:</strong> {{ timezone }}</p>
  <p><strong>Snapshot checksum:</strong> {{ checksum }}</p>
</header>
{% if metrics %}
<section aria-labelledby="metrics-heading">
  <h2 id="metrics-heading">Deterministic metrics</h2>
  <table><thead><tr><th>Metric</th><th>Value</th><th>Definition and timezone</th></tr></thead><tbody>
  {% for metric in metrics %}<tr><th scope="row">{{ metric.name }}</th><td><pre>{{ metric.value }}</pre></td><td>{{ metric.definition }}<br>{{ metric.timezone }}</td></tr>{% endfor %}
  </tbody></table>
</section>
{% endif %}
{% for category in categories %}
<section class="{{ category.css_class }}" aria-labelledby="category-{{ category.key }}">
  <h2 id="category-{{ category.key }}">{{ category.label }}</h2>
  {% if category.key == "plan" %}<p class="boundary"><strong>Approved plan only.</strong> This section is not inferred from recorded doses.</p>{% endif %}
  {% if category.key == "ai" %}<p><strong>Generated content—not a recorded fact or physician-approved instruction.</strong> Review against the cited sources.</p>{% endif %}
  {% if category.records %}{% for record in category.records %}<article class="record"><pre>{{ record }}</pre></article>{% endfor %}{% else %}<p>No selected records in this category for the reporting period.</p>{% endif %}
</section>
{% endfor %}
<footer class="provenance">
  <h2>Provenance</h2>
  <p>Render version: {{ render_version }}. Selected sections: {{ selected_sections }}.</p>
  {% for source in sources %}<p><strong>{{ source.label }} source IDs:</strong> {{ source.ids }}</p>{% endfor %}
  <p>Missing records and unavailable measurements are not interpreted as zero.</p>
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
    content = payload["snapshot_content"]
    manifest = payload["source_manifest"]
    metrics = payload["metric_values"]
    display_day = date.fromisoformat(payload["date_to"])
    categories = []
    for key in PARTITIONS:
        if key == "ai" and not payload["include_ai"]:
            continue
        css_class = (
            "ai-boundary" if key == "ai" else "patient-boundary" if key == "patient_note" else ""
        )
        categories.append(
            {
                "key": key,
                "label": _CATEGORY_LABELS[key],
                "css_class": css_class,
                "records": [_canonical_json(record, pretty=True) for record in content[key]],
            }
        )
    metric_rows = []
    for name, metric in sorted(metrics.items()):
        metric_rows.append(
            {
                "name": name.replace("_", " ").title(),
                "definition": metric["definition"],
                "timezone": timezone_abbreviation_for_local_date(metric["timezone"], display_day),
                "value": _canonical_json(
                    {
                        key: value
                        for key, value in metric.items()
                        if key not in {"definition", "timezone"}
                    },
                    pretty=True,
                ),
            }
        )
    html = _HTML.render(
        date_from=payload["date_from"],
        date_to=payload["date_to"],
        timezone=timezone_abbreviation_for_local_date(payload["timezone"], display_day),
        checksum=snapshot.canonical_sha256,
        metrics=metric_rows,
        categories=categories,
        sources=[
            {
                "label": _CATEGORY_LABELS[key],
                "ids": ", ".join(manifest[key]) if manifest[key] else "none",
            }
            for key in PARTITIONS
            if key != "ai" or payload["include_ai"]
        ],
        render_version=payload["render_version"],
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
