"""Human-readable presentation of immutable report snapshot data.

The canonical snapshot remains the audit source.  These helpers deliberately
summarize that frozen data for people instead of printing its JSON structure.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def _value(value: object) -> str:
    if value is None or value == "":
        return "Not recorded"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value).replace("_", " ")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return text
    if number == number.to_integral():
        return str(number.quantize(Decimal("1")))
    return format(number.normalize(), "f")


def _time(record: dict[str, Any], key: str = "local_time") -> str:
    raw = record.get(key) or record.get("occurred_at") or ""
    text = str(raw).replace("T", " ")
    return text[:16] if len(text) >= 16 else text or "Not recorded"


def _notes(record: dict[str, Any], *keys: str) -> str:
    values = [_value(record.get(key)) for key in keys if record.get(key) not in {None, ""}]
    return "; ".join(values) if values else ""


def _hours(seconds: object) -> str:
    value = Decimal(str(seconds or 0)) / Decimal(3600)
    rounded = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{_value(rounded)} hours"


def _distance_miles(value: object) -> str | None:
    if value is None or value == "":
        return None
    try:
        rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return f"{value} mi"
    return f"{rounded:.2f} mi"


def _activity_details(record: dict[str, Any]) -> str:
    details = []
    distance = _distance_miles(record.get("distance_miles"))
    if distance is not None:
        details.append(distance)
    details.extend(
        _value(record.get(key))
        for key in ("elapsed_seconds", "average_heart_rate", "maximum_heart_rate")
        if record.get(key) not in {None, ""}
    )
    return "; ".join(details)


def _table(
    title: str, headers: list[str], rows: list[list[str]], empty: str, section: str
) -> dict[str, Any]:
    return {"title": title, "headers": headers, "rows": rows, "empty": empty, "section": section}


def _metric_overview(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    daily = metrics.get("daily_doses") or {}
    daily_rows = daily.get("values") if isinstance(daily, dict) else []
    if isinstance(daily_rows, list):
        lines = []
        for row in daily_rows:
            if not isinstance(row, dict):
                continue
            actual = _value(row.get("actual_total"))
            planned = _value(row.get("planned_total"))
            unit = _value(row.get("unit")) if row.get("unit") else ""
            dose_count = _value(row.get("recorded_dose_count"))
            plan_text = (
                "no approved plan"
                if row.get("planned_total") is None
                else f"planned {planned} {unit}"
            )
            lines.append(
                f"{row.get('date')}: recorded {actual} {unit} in {dose_count} dose(s); {plan_text}"
            )
        result.append(
            {"title": "Daily dose record", "lines": lines or ["No daily dose metrics available."]}
        )

    symptoms = metrics.get("symptoms") or {}
    if isinstance(symptoms, dict):
        frequency = symptoms.get("frequency") or {}
        names = ", ".join(
            f"{name!s} ({_value(count)})" for name, count in sorted(frequency.items())
        )
        average = _value(symptoms.get("average_severity"))
        severity = (
            f"Average recorded severity {average}/10"
            if symptoms.get("average_severity") is not None
            else "No symptoms had a recorded severity"
        )
        result.append(
            {
                "title": "Symptoms",
                "lines": [
                    f"{_value(symptoms.get('count'))} recorded symptom(s): {names or 'none'}",
                    (
                        f"{severity}; {_value(symptoms.get('missing_count'))} "
                        "missing severity value(s)"
                    ),
                ],
            }
        )

    episodes = metrics.get("episodes") or {}
    if isinstance(episodes, dict):
        duration = episodes.get("average_duration_minutes")
        duration_text = (
            "Average duration unavailable"
            if duration is None
            else f"Average resolved duration {_value(duration)} minutes"
        )
        result.append(
            {
                "title": "Stress episodes",
                "lines": [
                    f"{_value(episodes.get('count'))} episode(s); {duration_text}",
                    f"{_value(episodes.get('missing_count'))} open or otherwise missing duration",
                ],
            }
        )

    timing = metrics.get("timing") or {}
    periods = timing.get("values") if isinstance(timing, dict) else []
    if isinstance(periods, list) and periods:
        totals = Counter()
        for period in periods:
            if not isinstance(period, dict):
                continue
            for key in ("matched_count", "on_time", "early", "late", "unplanned", "missing_count"):
                totals[key] += int(period.get(key) or 0)
        result.append(
            {
                "title": "Recorded dose timing vs approved plan",
                "lines": [
                    (
                        f"{totals['matched_count']} matched: {totals['on_time']} on time, "
                        f"{totals['early']} early, {totals['late']} late"
                    ),
                    (
                        f"{totals['unplanned']} recorded dose(s) unmatched to a plan slot; "
                        f"{totals['missing_count']} slot(s) without a matched dose"
                    ),
                ],
            }
        )
    return result


def _wearable_rows(
    records: list[dict[str, Any]], metrics: dict[str, Any] | None = None
) -> list[list[str]]:
    summary_metric = (metrics or {}).get("wearable_daily_summaries") or {}
    summaries = summary_metric.get("values") if isinstance(summary_metric, dict) else []
    if isinstance(summaries, list) and summaries:
        rows = []
        for row in summaries:
            if not isinstance(row, dict):
                continue
            observed = (
                "missing"
                if row.get("average") is None
                else (
                    f"avg {_value(row.get('average'))}; low {_value(row.get('minimum'))}; "
                    f"high {_value(row.get('maximum'))}"
                )
            )
            rows.append(
                [
                    str(row.get("date") or ""),
                    str(row.get("metric_type") or "metric").replace("_", " ").title(),
                    observed,
                    str(row.get("unit") or "").replace("_", " "),
                    f"{int(row.get('sample_count') or 0):,}",
                ]
            )
        return rows
    grouped: dict[tuple[str, str, str], list[Decimal]] = defaultdict(list)
    for record in records:
        if record.get("record_type") != "garmin_metric":
            continue
        try:
            number = Decimal(str(record.get("value")))
        except InvalidOperation:
            continue
        grouped[
            (
                _time(record)[:10],
                str(record.get("metric_type") or "metric"),
                str(record.get("unit") or ""),
            )
        ].append(number)
    rows = []
    for (day, metric, unit), values in sorted(grouped.items()):
        average = (sum(values) / Decimal(len(values))).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        rows.append(
            [
                day,
                metric.replace("_", " ").title(),
                f"avg {_value(average)}; low {_value(min(values))}; high {_value(max(values))}",
                unit.replace("_", " "),
                f"{len(values):,}",
            ]
        )
    return rows


def _garmin_aggregate_rows(records: list[dict[str, Any]]) -> list[list[str]]:
    return [
        [
            _time(record),
            str(record.get("metric_type") or "metric").replace("_", " ").title(),
            f"{_value(record.get('value'))} {_value(record.get('unit'))}",
            str(record.get("aggregation") or "aggregate").replace("_", " ").title(),
        ]
        for record in records
        if record.get("record_type") == "garmin_metric_aggregate"
    ]


def presentation(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload["snapshot_content"]
    facts = [record for record in content["fact"] if isinstance(record, dict)]
    plans = [record for record in content["plan"] if isinstance(record, dict)]
    patient_notes = [record for record in content["patient_note"] if isinstance(record, dict)]
    analyses = [record for record in content["ai"] if isinstance(record, dict)]
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in facts:
        by_type[str(record.get("record_type") or "other")].append(record)

    wearable_metric = payload["metric_values"].get("wearable_daily_summaries") or {}
    wearable_values = wearable_metric.get("values") if isinstance(wearable_metric, dict) else []
    wearable_count = (
        sum(int(row.get("sample_count") or 0) for row in wearable_values if isinstance(row, dict))
        if isinstance(wearable_values, list)
        else len(by_type["garmin_metric"])
    ) + len(by_type["garmin_metric_aggregate"])
    summary = [
        {"label": "Recorded doses", "value": len(by_type["dose"])},
        {"label": "Symptoms", "value": len(by_type["symptom"])},
        {"label": "Stress episodes", "value": len(by_type["stress_episode"])},
        {"label": "Emergency injections", "value": len(by_type["emergency_injection"])},
        {"label": "Garmin observations", "value": f"{wearable_count:,}"},
        {"label": "Approved plans", "value": len(plans)},
    ]

    dose_rows = [
        [
            _time(record),
            _value(record.get("medication_name")),
            f"{_value(record.get('amount'))} {_value(record.get('unit'))}",
            _value(record.get("route")).title(),
            _value(record.get("category")).title(),
            _notes(record, "notes"),
        ]
        for record in by_type["dose"]
    ]
    symptom_rows = [
        [
            _time(record),
            _value(record.get("name")),
            "Not recorded"
            if record.get("severity") is None
            else f"{_value(record.get('severity'))}/10",
            _value(record.get("body_area")),
            _notes(record, "notes"),
        ]
        for record in by_type["symptom"]
    ]
    episode_rows = [
        [
            _time(record, "started_at"),
            _value(record.get("trigger")),
            _value(record.get("severity")).title(),
            _value(record.get("status")).title(),
            _notes(record, "illness_description", "highest_temperature_c", "outcome", "notes"),
        ]
        for record in by_type["stress_episode"]
    ]
    vital_rows = []
    for record in [
        *by_type["blood_pressure"],
        *by_type["weight"],
        *by_type["temperature"],
    ]:
        if record.get("record_type") == "blood_pressure":
            reading = (
                f"{_value(record.get('systolic_mmhg'))}/{_value(record.get('diastolic_mmhg'))} mmHg"
            )
            if record.get("pulse_bpm") is not None:
                reading += f"; pulse {_value(record.get('pulse_bpm'))} bpm"
            kind = "Blood pressure"
        elif record.get("record_type") == "weight":
            kind = "Weight"
            reading = f"{_value(record.get('value'))} {_value(record.get('unit'))}"
        else:
            kind = "Temperature"
            reading = f"{_value(record.get('display_f'))} °F ({_value(record.get('display_c'))} °C)"
        setting = (
            _value(record.get("measurement_setting")).title()
            if record.get("record_type") in {"blood_pressure", "weight"}
            else ""
        )
        vital_rows.append([_time(record), kind, reading, setting, _notes(record, "notes")])

    injection_rows = [
        [
            _time(record),
            (
                f"{_value(record.get('amount'))} {_value(record.get('unit'))} "
                f"{_value(record.get('route'))}"
            ),
            _value(record.get("reason")),
            _notes(record, "response", "notes"),
        ]
        for record in by_type["emergency_injection"]
    ]
    life_rows = [
        [
            _time(record),
            _value(record.get("title")),
            _value(record.get("category")).title(),
            _value(record.get("description")),
        ]
        for record in by_type["life_event"]
    ]
    activity_rows = [
        [
            _time(record),
            _value(record.get("sport")).title(),
            _value(record.get("title")),
            _activity_details(record),
        ]
        for record in by_type["garmin_activity"]
    ]
    sleep_rows = [
        [
            _time(record),
            _hours(record.get("duration_seconds")),
            _value(record.get("overall_sleep_score")),
            _value(record.get("awakenings")),
        ]
        for record in by_type["garmin_sleep"]
    ]
    lab_rows = []
    for panel in by_type["lab_panel"]:
        for result in panel.get("results") or []:
            if not isinstance(result, dict):
                continue
            result_value = result.get("original_value") or result.get("qualitative_result")
            original_unit = (
                _value(result.get("original_unit")) if result.get("original_unit") else ""
            )
            lab_rows.append(
                [
                    _time(panel),
                    _value(result.get("analyte_name")),
                    f"{_value(result_value)} {original_unit}".strip(),
                    _value(result.get("original_reference_range")),
                    _value(result.get("abnormal_flag")),
                ]
            )

    tables = [
        _table(
            "Recorded doses",
            ["Local time", "Medication", "Amount", "Route", "Category", "Notes"],
            dose_rows,
            "No recorded doses selected for this period.",
            "doses",
        ),
        _table(
            "Symptoms",
            ["Local time", "Symptom", "Severity", "Body area", "Notes"],
            symptom_rows,
            "No symptoms selected for this period.",
            "symptoms",
        ),
        _table(
            "Stress episodes",
            ["Started", "Trigger", "Severity", "Status", "Context"],
            episode_rows,
            "No stress episodes selected for this period.",
            "episodes",
        ),
        _table(
            "Vitals",
            ["Local time", "Measurement", "Value", "Setting", "Notes"],
            vital_rows,
            "No blood pressure, weight, or temperature records selected for this period.",
            "vitals",
        ),
        _table(
            "Emergency injections",
            ["Local time", "Injection", "Reason", "Response and notes"],
            injection_rows,
            "No emergency injections selected for this period.",
            "emergency_injections",
        ),
        _table(
            "Laboratory results",
            ["Specimen time", "Analyte", "Result", "Reference range", "Flag"],
            lab_rows,
            "No laboratory results selected for this period.",
            "labs",
        ),
        _table(
            "Garmin daily observation summary",
            ["Date", "Metric", "Observed range", "Unit", "Samples"],
            _wearable_rows(facts, payload["metric_values"]),
            "No Garmin metric observations selected for this period.",
            "wearables",
        ),
        _table(
            "Garmin provider aggregates",
            ["Recorded local time", "Metric", "Provider value", "Period type"],
            _garmin_aggregate_rows(facts),
            "No separate Garmin provider aggregates selected for this period.",
            "wearables",
        ),
        _table(
            "Garmin sleep",
            ["Started", "Duration", "Sleep score", "Awakenings"],
            sleep_rows,
            "No Garmin sleep sessions selected for this period.",
            "wearables",
        ),
        _table(
            "Garmin activities",
            ["Started", "Sport", "Activity", "Recorded details"],
            activity_rows,
            "No Garmin activities selected for this period.",
            "wearables",
        ),
        _table(
            "Life events",
            ["Local time", "Event", "Category", "Description"],
            life_rows,
            "No life events selected for this period.",
            "life_events",
        ),
    ]

    plan_rows = []
    instruction_rows = []
    for plan in plans:
        label = _value(plan.get("version_label"))
        for slot in plan.get("slots") or []:
            if isinstance(slot, dict):
                timing = _value(slot.get("scheduled_local_time"))[:5]
                if slot.get("timing_mode") == "wake":
                    reminder = _value(slot.get("reminder_local_time"))[:5]
                    timing = f"When waking; remind by {reminder}"
                plan_rows.append(
                    [
                        label,
                        timing,
                        _value(slot.get("medication_name")),
                        f"{_value(slot.get('amount'))} {_value(slot.get('unit'))}",
                        _value(slot.get("route")).title(),
                        _value(slot.get("condition")),
                    ]
                )
        for instruction in plan.get("instructions") or []:
            if isinstance(instruction, dict):
                instruction_rows.append(
                    [
                        label,
                        _value(instruction.get("category")).title(),
                        _value(instruction.get("title")),
                        _value(instruction.get("body")),
                    ]
                )

    note_rows = [
        [_time(record), _value(record.get("text")), _value(record.get("tags"))]
        for record in patient_notes
    ]
    ai_rows = [
        [
            _time(record, "generated_at"),
            _value(record.get("analysis_type")).title(),
            _value(record.get("body")),
        ]
        for record in analyses
    ]
    return {
        "summary": summary,
        "metric_overview": _metric_overview(payload["metric_values"]),
        "tables": [table for table in tables if table["section"] in payload["selected_sections"]],
        "plan_table": _table(
            "Approved medication schedule",
            ["Plan", "Time", "Medication", "Amount", "Route", "Condition"],
            plan_rows,
            "No physician-approved medication schedule selected for this period.",
            "approved_plan",
        ),
        "instruction_table": _table(
            "Approved instructions",
            ["Plan", "Category", "Title", "Instruction"],
            instruction_rows,
            "No approved instructions selected for this period.",
            "approved_plan",
        ),
        "note_table": _table(
            "Patient notes and questions",
            ["Local time", "Entry", "Tags"],
            note_rows,
            "No patient notes selected for this period.",
            "patient_notes",
        ),
        "ai_table": _table(
            "AI-generated analysis",
            ["Generated", "Type", "Analysis"],
            ai_rows,
            "No eligible AI analysis selected for this period.",
            "ai",
        ),
        "partition_counts": {
            key: len(content[key]) for key in ("fact", "plan", "patient_note", "ai")
        },
    }
