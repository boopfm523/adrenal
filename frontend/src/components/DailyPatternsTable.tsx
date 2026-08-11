import { useState } from "react";

import { downloadDailyPatternsCsv, type DailyPatterns } from "../api/client";
import { formatDecimal, formatMeasurement } from "../format";

type Day = DailyPatterns["days"][number];
type Wearable = Day["wearables"][number];

function wearable(day: Day, metricType: Wearable["metric_type"]): Wearable | undefined {
  return day.wearables.find((entry) => entry.metric_type === metricType);
}

function rangeText(entry: Wearable | undefined): string {
  if (entry === undefined || entry.sample_count === 0) return "Missing—0 samples";
  if (entry.incompatible_units) return `${entry.sample_count.toString()} samples; unavailable—mixed units`;
  const values = `${formatMeasurement(entry.minimum, entry.unit)}–${formatMeasurement(entry.maximum, entry.unit)}; average ${formatMeasurement(entry.average, entry.unit)}`;
  const cadence = entry.samples_without_cadence === 0 ? "all samples supplied cadence" : `${entry.samples_without_cadence.toString()} without cadence`;
  return `${values}; ${entry.sample_count.toString()} samples; ${formatDecimal(entry.observed_coverage_percent)}% observed cadence coverage; ${cadence}`;
}

function bloodPressureText(day: Day): string {
  const metric = day.blood_pressure;
  if (metric.sample_count === 0) return "Missing—no readings";
  const pulse = metric.pulse_sample_count === 0
    ? "pulse missing"
    : `pulse ${formatMeasurement(metric.pulse.minimum, "bpm")}–${formatMeasurement(metric.pulse.maximum, "bpm")}; ${metric.pulse_missing_count.toString()} missing pulse`;
  return `systolic ${formatMeasurement(metric.systolic.minimum, "mmHg")}–${formatMeasurement(metric.systolic.maximum, "mmHg")}; diastolic ${formatMeasurement(metric.diastolic.minimum, "mmHg")}–${formatMeasurement(metric.diastolic.maximum, "mmHg")}; ${metric.sample_count.toString()} readings; ${pulse}`;
}

function symptomText(day: Day): string {
  if (day.symptom_count === 0) return "Missing—no symptom facts";
  return `${day.symptom_count.toString()} facts; average recorded severity ${formatDecimal(day.average_symptom_severity)}; ${day.symptom_severity_missing_count.toString()} missing severity`;
}

export function DailyPatternsTable({ data }: { data: DailyPatterns }): React.JSX.Element {
  const [exportState, setExportState] = useState<"idle" | "pending" | "error">("idle");

  async function exportCsv(): Promise<void> {
    setExportState("pending");
    try {
      const blob = await downloadDailyPatternsCsv(data.date_from, data.date_to, data.timezone);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `healthcurve-daily-patterns-${data.date_from}-${data.date_to}.csv`;
      anchor.click();
      URL.revokeObjectURL(url);
      setExportState("idle");
    } catch {
      setExportState("error");
    }
  }

  return <section className="metric-card daily-patterns" aria-labelledby="daily-patterns-title">
    <div className="section-heading-row"><div><h2 id="daily-patterns-title">Compare daily patterns</h2><p>{data.safety_label}</p></div><button type="button" onClick={() => { void exportCsv(); }} disabled={exportState === "pending"}>{exportState === "pending" ? "Preparing CSV…" : "Download daily features CSV"}</button></div>
    {exportState === "error" ? <p className="error-summary" role="alert">The daily-feature CSV could not be downloaded.</p> : null}
    <dl className="metric-metadata"><div><dt>Date range</dt><dd>{data.date_from} through {data.date_to}</dd></div><div><dt>Timezone</dt><dd>{data.timezone}</dd></div><div><dt>Feature version</dt><dd>{data.feature_version}</dd></div><div><dt>Exposure model versions</dt><dd>{data.exposure_model_versions.join(", ")}</dd></div></dl>
    <aside className="association-caution"><strong>Comparable does not mean causal.</strong> Each row describes one local day using current facts and its own explicit data availability. Different plan or model versions remain labeled per row.</aside>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Daily pattern exact values"><table className="daily-pattern-table"><caption>Deterministic daily features. Missing values remain missing; revision fingerprints identify the exact current source projection.</caption><thead><tr><th scope="col">Local date</th><th scope="col">Exposure</th><th scope="col">Actual-dose context</th><th scope="col">Symptoms</th><th scope="col">Garmin stress</th><th scope="col">Heart rate</th><th scope="col">HRV</th><th scope="col">Respiration</th><th scope="col">Blood pressure</th><th scope="col">Stress episodes</th><th scope="col">Versions / revision</th></tr></thead><tbody>{data.days.map((day) => <tr key={day.date}><th scope="row">{day.date}<span className="table-secondary">{formatDecimal(day.elapsed_hours)} elapsed hours</span></th><td>Peak {formatMeasurement(day.exposure_peak_reu, "REU")}<span className="table-secondary">AUC {formatMeasurement(day.exposure_auc_reu_hours, "REU-hours")}</span></td><td>{day.supported_dose_count.toString()} supported; {day.excluded_dose_count.toString()} excluded<span className="table-secondary">{day.dose_plan_version_ids.length.toString()} linked plan version(s)</span></td><td>{symptomText(day)}</td><td>{rangeText(wearable(day, "stress"))}</td><td>{rangeText(wearable(day, "heart_rate"))}</td><td>{rangeText(wearable(day, "hrv"))}</td><td>{rangeText(wearable(day, "respiration_rate"))}</td><td>{bloodPressureText(day)}</td><td>{day.stress_episodes.count.toString()} episodes; {formatMeasurement(day.stress_episodes.overlap_minutes, "minutes")} overlap<span className="table-secondary">{day.stress_episodes.open_count.toString()} open</span></td><td>{day.feature_version}<span className="table-secondary">{day.exposure_model_version}</span><code className="revision-watermark">{day.source_revision_watermark_sha256}</code></td></tr>)}</tbody></table></div>
    <details className="metric-definition"><summary>Definitions, symptom timing, and source coverage</summary>{Object.entries(data.definitions).map(([name, definition]) => <p key={name}><strong>{name.replaceAll("_", " ")}:</strong> {definition}</p>)}{data.days.flatMap((day) => day.symptom_timings.map((symptom) => <p key={symptom.symptom_event_id}><strong>{day.date} · {symptom.name}:</strong> severity {symptom.severity == null ? "missing" : `${symptom.severity.toString()}/10`}; {symptom.minutes_since_previous_supported_dose == null ? "no supported preceding dose in the model horizon" : `${formatDecimal(symptom.minutes_since_previous_supported_dose)} minutes since the latest supported dose`}; theoretical exposure {formatMeasurement(symptom.theoretical_exposure_reu, "REU")}.</p>))}</details>
  </section>;
}
