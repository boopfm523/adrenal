import { useState } from "react";
import { Link } from "react-router-dom";

import { deletePatternAnalysis, downloadDailyPatternsCsv, generatePatternAnalysis, type DailyPatterns, type PatternAnalysis } from "../api/client";
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

function signedValue(value: string | null, unit: string): string {
  if (value === null) return "Withheld—fewer than 7 observed days";
  const numeric = Number(value);
  return `${numeric > 0 ? "+" : ""}${formatMeasurement(value, unit)}`;
}

export function DailyPatternsTable({ data }: { data: DailyPatterns }): React.JSX.Element {
  const [exportState, setExportState] = useState<"idle" | "pending" | "error">("idle");
  const [analysisState, setAnalysisState] = useState<"idle" | "pending" | "error">("idle");
  const [analysis, setAnalysis] = useState<PatternAnalysis | null>(null);
  const [analysisNotice, setAnalysisNotice] = useState<string | null>(null);

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

  async function createAnalysis(): Promise<void> {
    setAnalysisState("pending");
    setAnalysisNotice(null);
    try {
      const result = await generatePatternAnalysis(data.date_from, data.date_to, data.timezone);
      setAnalysis(result.analysis ?? null);
      setAnalysisNotice(result.detail ?? null);
      setAnalysisState("idle");
    } catch {
      setAnalysisState("error");
    }
  }

  async function removeAnalysis(): Promise<void> {
    if (analysis === null) return;
    setAnalysisState("pending");
    try {
      await deletePatternAnalysis(analysis.id);
      setAnalysis(null);
      setAnalysisNotice("The generated draft was deleted. Recorded facts and plans were unchanged.");
      setAnalysisState("idle");
    } catch {
      setAnalysisState("error");
    }
  }

  return <section className="metric-card daily-patterns" aria-labelledby="daily-patterns-title">
    <div className="section-heading-row"><div><h2 id="daily-patterns-title">Compare daily patterns</h2><p>{data.safety_label}</p></div><button type="button" onClick={() => { void exportCsv(); }} disabled={exportState === "pending"}>{exportState === "pending" ? "Preparing CSV…" : "Download daily features CSV"}</button></div>
    {exportState === "error" ? <p className="error-summary" role="alert">The daily-feature CSV could not be downloaded.</p> : null}
    <dl className="metric-metadata"><div><dt>Date range</dt><dd>{data.date_from} through {data.date_to}</dd></div><div><dt>Timezone</dt><dd>{data.timezone}</dd></div><div><dt>Feature version</dt><dd>{data.feature_version}</dd></div><div><dt>Exposure model versions</dt><dd>{data.exposure_model_versions.join(", ")}</dd></div></dl>
    <aside className="association-caution"><strong>Comparable does not mean causal.</strong> Each row describes one local day using current facts and its own explicit data availability. Different plan or model versions remain labeled per row.</aside>
    <section aria-labelledby="longitudinal-summary-title"><h3 id="longitudinal-summary-title">Range distributions and trends</h3><p>{data.longitudinal_summary.coverage_definition}</p><aside className="association-caution">{data.longitudinal_summary.multiple_comparison_caution}</aside><div className="table-scroll" tabIndex={0} role="region" aria-label="Longitudinal pattern summary"><table><caption>One deterministic value per local day. Trends require at least {data.longitudinal_summary.minimum_observed_days_for_trend.toString()} observed days.</caption><thead><tr><th scope="col">Metric</th><th scope="col">Observed coverage</th><th scope="col">Distribution</th><th scope="col">First to last</th></tr></thead><tbody>{data.longitudinal_summary.metrics.map((metric) => <tr key={metric.key}><th scope="row">{metric.label}<span className="table-secondary">{metric.unit}</span></th><td>{metric.observed_days.toString()} of {data.longitudinal_summary.total_days.toString()} days ({formatDecimal(metric.observed_day_percent)}%)<span className="table-secondary">{metric.missing_days.toString()} missing days</span></td><td>{metric.minimum === null ? "Missing—no observed days" : `${formatMeasurement(metric.minimum, metric.unit)} minimum; ${formatMeasurement(metric.median, metric.unit)} median; ${formatMeasurement(metric.maximum, metric.unit)} maximum`}</td><td>{signedValue(metric.first_to_last_change, metric.unit)}</td></tr>)}</tbody></table></div><details className="metric-definition"><summary>Model-version boundaries</summary>{data.longitudinal_summary.model_version_periods.map((period) => <p key={`${period.date_from}-${period.exposure_model_version}`}><strong>{period.date_from} through {period.date_to}:</strong> features {period.feature_version}; exposure {period.exposure_model_version}.</p>)}</details></section>
    <section className="category-card category-card--ai" aria-labelledby="pattern-analysis-title"><h3 id="pattern-analysis-title">Optional private-model pattern draft</h3><p>Ask the configured private model to phrase only the deterministic figures above. The draft must cite every daily feature source, carries model/prompt/schema provenance, and cannot change facts or plans.</p><button type="button" onClick={() => { void createAnalysis(); }} disabled={analysisState === "pending"}>{analysisState === "pending" ? "Generating checked draft…" : "Generate cited pattern draft"}</button>{analysisState === "error" ? <p className="error-summary" role="alert">The pattern draft request failed. Deterministic results remain available.</p> : null}{analysisNotice === null ? null : <p role="status">{analysisNotice}</p>}{analysis === null ? null : <div><aside className="draft-warning">Generated analysis—not medical advice, a diagnosis, or a physician-approved plan.</aside><pre className="report-record">{analysis.body}</pre><p><strong>Sources:</strong> {analysis.source_record_ids.length.toString()} daily feature IDs. <strong>Model:</strong> {analysis.model_name} ({analysis.model_digest}). <strong>Prompt/schema:</strong> {analysis.prompt_version} / {analysis.schema_version}.</p><button type="button" onClick={() => { void removeAnalysis(); }} disabled={analysisState === "pending"}>Delete generated draft</button></div>}</section>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Daily pattern exact values"><table className="daily-pattern-table"><caption>Deterministic daily features. Missing values remain missing; revision fingerprints identify the exact current source projection.</caption><thead><tr><th scope="col">Local date</th><th scope="col">Exposure</th><th scope="col">Actual-dose context</th><th scope="col">Symptoms</th><th scope="col">Garmin stress</th><th scope="col">Heart rate</th><th scope="col">HRV</th><th scope="col">Respiration</th><th scope="col">Blood pressure</th><th scope="col">Stress episodes</th><th scope="col">Versions / revision</th></tr></thead><tbody>{data.days.map((day) => <tr key={day.date}><th scope="row"><Link to={`/healthcurve?day=${day.date}&timezone=${encodeURIComponent(data.timezone)}`}>{day.date}</Link><span className="table-secondary">Review this day’s HealthCurve</span><span className="table-secondary">{formatDecimal(day.elapsed_hours)} elapsed hours</span></th><td>Peak {formatMeasurement(day.exposure_peak_reu, "REU")}<span className="table-secondary">AUC {formatMeasurement(day.exposure_auc_reu_hours, "REU-hours")}</span></td><td>{day.supported_dose_count.toString()} supported; {day.excluded_dose_count.toString()} excluded<span className="table-secondary">{day.dose_plan_version_ids.length.toString()} linked plan version(s)</span></td><td>{symptomText(day)}</td><td>{rangeText(wearable(day, "stress"))}</td><td>{rangeText(wearable(day, "heart_rate"))}</td><td>{rangeText(wearable(day, "hrv"))}</td><td>{rangeText(wearable(day, "respiration_rate"))}</td><td>{bloodPressureText(day)}</td><td>{day.stress_episodes.count.toString()} episodes; {formatMeasurement(day.stress_episodes.overlap_minutes, "minutes")} overlap<span className="table-secondary">{day.stress_episodes.open_count.toString()} open</span></td><td>{day.feature_version}<span className="table-secondary">{day.exposure_model_version}</span><code className="revision-watermark">{day.source_revision_watermark_sha256}</code></td></tr>)}</tbody></table></div>
    <details className="metric-definition"><summary>Definitions, symptom timing, and source coverage</summary>{Object.entries(data.definitions).map(([name, definition]) => <p key={name}><strong>{name.replaceAll("_", " ")}:</strong> {definition}</p>)}{data.days.flatMap((day) => day.symptom_timings.map((symptom) => <p key={symptom.symptom_event_id}><strong>{day.date} · {symptom.name}:</strong> severity {symptom.severity == null ? "missing" : `${symptom.severity.toString()}/10`}; {symptom.minutes_since_previous_supported_dose == null ? "no supported preceding dose in the model horizon" : `${formatDecimal(symptom.minutes_since_previous_supported_dose)} minutes since the latest supported dose`}; theoretical exposure {formatMeasurement(symptom.theoretical_exposure_reu, "REU")}.</p>))}</details>
  </section>;
}
