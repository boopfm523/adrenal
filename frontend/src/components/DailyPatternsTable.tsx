import { useState } from "react";
import { Link } from "react-router-dom";

import { downloadDailyPatternsCsv, type DailyPatterns } from "../api/client";
import { formatDecimal, formatMeasurement, formatRoundedDecimal, humanizeUnit } from "../format";
import { timezoneAbbreviationForLocalDate } from "../time";
import { PatternAnalysisCard } from "./PatternAnalysisCard";

type Day = DailyPatterns["days"][number];
type Wearable = Day["wearables"][number];

function wearable(day: Day, metricType: Wearable["metric_type"]): Wearable | undefined {
  return day.wearables.find((entry) => entry.metric_type === metricType);
}

function rangeText(entry: Wearable | undefined): string {
  if (entry === undefined || entry.sample_count === 0) return "No samples recorded";
  if (entry.incompatible_units) return `${entry.sample_count.toString()} samples; unavailable—mixed units`;
  const values = `${longitudinalMeasurement(entry.minimum, entry.unit)}–${longitudinalMeasurement(entry.maximum, entry.unit)}; average ${longitudinalMeasurement(entry.average, entry.unit)}`;
  const cadence = entry.samples_without_cadence === 0 ? "all samples supplied cadence" : `${entry.samples_without_cadence.toString()} without cadence`;
  return `${values}; ${entry.sample_count.toString()} samples; ${formatRoundedDecimal(entry.observed_coverage_percent, 0)}% observed cadence coverage; ${cadence}`;
}

function bloodPressureText(day: Day): string {
  const metric = day.blood_pressure;
  if (metric.sample_count === 0) return "No readings recorded";
  const pulse = metric.pulse_sample_count === 0
    ? "pulse missing"
    : `pulse ${formatMeasurement(metric.pulse.minimum, "bpm")}–${formatMeasurement(metric.pulse.maximum, "bpm")}; ${metric.pulse_missing_count.toString()} missing pulse`;
  return `systolic ${formatMeasurement(metric.systolic.minimum, "mmHg")}–${formatMeasurement(metric.systolic.maximum, "mmHg")}; diastolic ${formatMeasurement(metric.diastolic.minimum, "mmHg")}–${formatMeasurement(metric.diastolic.maximum, "mmHg")}; ${metric.sample_count.toString()} readings; ${pulse}`;
}

function symptomText(day: Day): string {
  if (day.symptom_count === 0) return "No symptoms recorded";
  return `${day.symptom_count.toString()} facts; average recorded severity ${formatRoundedDecimal(day.average_symptom_severity, 1)}; ${day.symptom_severity_missing_count.toString()} missing severity`;
}

function hasMeaningfulData(day: Day): boolean {
  return day.supported_dose_count > 0
    || day.excluded_dose_count > 0
    || day.symptom_count > 0
    || day.stress_episodes.count > 0
    || day.blood_pressure.sample_count > 0
    || day.wearables.some((entry) => entry.sample_count > 0);
}

function exposureText(day: Day): React.JSX.Element {
  if (Number(day.exposure_peak_reu) === 0 && Number(day.exposure_auc_reu_hours) === 0) {
    return <>No modeled exposure<span className="table-secondary">No supported dose contribution during this day</span></>;
  }
  return <>Peak {longitudinalMeasurement(day.exposure_peak_reu, "REU")}<span className="table-secondary">AUC {longitudinalMeasurement(day.exposure_auc_reu_hours, "REU-hours")}</span></>;
}

function doseText(day: Day): React.JSX.Element {
  if (day.supported_dose_count === 0 && day.excluded_dose_count === 0) return <>No doses recorded</>;
  return <>{day.supported_dose_count.toString()} modeled; {day.excluded_dose_count.toString()} not modeled<span className="table-secondary">{day.dose_plan_version_ids.length.toString()} linked approved plan version(s)</span></>;
}

function episodeText(day: Day): React.JSX.Element {
  if (day.stress_episodes.count === 0) return <>No episodes recorded</>;
  return <>{day.stress_episodes.count.toString()} episodes; {formatMeasurement(day.stress_episodes.overlap_minutes, "minutes")} overlap<span className="table-secondary">{day.stress_episodes.open_count.toString()} open</span></>;
}

function signedValue(value: string | null, unit: string): string {
  if (value === null) return "Withheld—fewer than 7 observed days";
  const numeric = Number(value);
  const formatted = formatRoundedDecimal(value, 1);
  const signed = numeric > 0 ? `+${formatted}` : numeric < 0 ? `−${formatted.replace(/^-/, "")}` : formatted;
  return `${signed} ${humanizeUnit(unit)}`;
}

function longitudinalMeasurement(value: string | null, unit: string | null): string {
  return `${formatRoundedDecimal(value, 1)} ${humanizeUnit(unit)}`;
}

export function DailyPatternsTable({ data }: { data: DailyPatterns }): React.JSX.Element {
  const [exportState, setExportState] = useState<"idle" | "pending" | "error">("idle");
  const [showAllDates, setShowAllDates] = useState(false);
  const [page, setPage] = useState(1);
  const meaningfulDays = data.days.filter(hasMeaningfulData);
  const omittedDays = data.days.filter((day) => !hasMeaningfulData(day));
  const displayedDays = showAllDates ? data.days : meaningfulDays;
  const pageSize = 31;
  const totalPages = Math.max(1, Math.ceil(displayedDays.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * pageSize;
  const visibleDays = displayedDays.slice(pageStart, pageStart + pageSize);

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
    <dl className="metric-metadata"><div><dt>Date range</dt><dd>{data.date_from} through {data.date_to}</dd></div><div><dt>Timezone</dt><dd>{timezoneAbbreviationForLocalDate(data.timezone, data.date_to)}</dd></div><div><dt>Feature version</dt><dd>{data.feature_version}</dd></div><div><dt>Exposure model versions</dt><dd>{data.exposure_model_versions.join(", ")}</dd></div></dl>
    <aside className="association-caution"><strong>Comparable does not mean causal.</strong> Each row describes one local day using current facts and its own explicit data availability. Different plan or model versions remain labeled per row.</aside>
    <section aria-labelledby="longitudinal-summary-title"><h3 id="longitudinal-summary-title">Range distributions and trends</h3><p>{data.longitudinal_summary.coverage_definition}</p><aside className="association-caution">{data.longitudinal_summary.multiple_comparison_caution}</aside><div className="table-scroll" tabIndex={0} role="region" aria-label="Longitudinal pattern summary"><table><caption>One deterministic value per local day. Trends require at least {data.longitudinal_summary.minimum_observed_days_for_trend.toString()} observed days.</caption><thead><tr><th scope="col">Metric</th><th scope="col">Observed coverage</th><th scope="col">Distribution</th><th scope="col">First to last</th></tr></thead><tbody>{data.longitudinal_summary.metrics.map((metric) => <tr key={metric.key}><th scope="row">{metric.label}<span className="table-secondary">{metric.unit}</span></th><td>{metric.observed_days.toString()} of {data.longitudinal_summary.total_days.toString()} days ({formatRoundedDecimal(metric.observed_day_percent, 0)}%)<span className="table-secondary">{metric.missing_days.toString()} missing days</span></td><td>{metric.minimum === null ? "Missing—no observed days" : `${longitudinalMeasurement(metric.minimum, metric.unit)} minimum; ${longitudinalMeasurement(metric.median, metric.unit)} median; ${longitudinalMeasurement(metric.maximum, metric.unit)} maximum`}</td><td>{signedValue(metric.first_to_last_change, metric.unit)}</td></tr>)}</tbody></table></div><details className="metric-definition"><summary>Model-version boundaries</summary>{data.longitudinal_summary.model_version_periods.map((period) => <p key={`${period.date_from}-${period.exposure_model_version}`}><strong>{period.date_from} through {period.date_to}:</strong> features {period.feature_version}; exposure {period.exposure_model_version}.</p>)}</details></section>
    <PatternAnalysisCard key={`${data.date_from}:${data.date_to}:${data.timezone}`} dateFrom={data.date_from} dateTo={data.date_to} timezone={data.timezone} />
    <section aria-labelledby="daily-details-title"><div className="section-heading-row"><div><h3 id="daily-details-title">Days with recorded health context</h3><p>Empty dates are hidden by default. A hidden date has no recorded dose, symptom, episode, blood-pressure reading, or wearable sample; missing data is not treated as zero.</p></div>{omittedDays.length === 0 ? null : <button type="button" className="button-secondary" aria-pressed={showAllDates} onClick={() => { setShowAllDates(!showAllDates); setPage(1); }}>{showAllDates ? `Hide ${omittedDays.length.toString()} empty dates` : `Show all ${data.days.length.toString()} dates`}</button>}</div>{omittedDays.length === 0 ? null : <p className="privacy-note">{omittedDays.length.toString()} empty date(s) hidden between {omittedDays[0]?.date} and {omittedDays.at(-1)?.date}.</p>}{visibleDays.length === 0 ? <div className="empty-state"><h4>No days contain recorded health context</h4><p>Use “Show all dates” to inspect explicit missing-data rows.</p></div> : <div className="standard-table-region standard-table-region--blue"><div className="table-scroll" tabIndex={0} role="region" aria-label="Daily pattern values"><table className="daily-pattern-table"><caption>One row per {showAllDates ? "selected" : "non-empty"} local day. Missing observations remain missing, never zero.</caption><thead><tr><th scope="col">Local date</th><th scope="col">Modeled medication exposure</th><th scope="col">Recorded doses</th><th scope="col">Symptoms</th><th scope="col">Garmin stress</th><th scope="col">Heart rate</th><th scope="col">HRV</th><th scope="col">Respiration</th><th scope="col">Blood pressure</th><th scope="col">Stress episodes</th></tr></thead><tbody>{visibleDays.map((day) => <tr key={day.date}><th scope="row"><Link to={`/healthcurve?day=${day.date}&timezone=${encodeURIComponent(data.timezone)}`}>{day.date}</Link><span className="table-secondary">Open this day’s HealthCurve</span><span className="table-secondary">{formatDecimal(day.elapsed_hours)} elapsed hours</span></th><td>{exposureText(day)}</td><td>{doseText(day)}</td><td>{symptomText(day)}</td><td>{rangeText(wearable(day, "stress"))}</td><td>{rangeText(wearable(day, "heart_rate"))}</td><td>{rangeText(wearable(day, "hrv"))}</td><td>{rangeText(wearable(day, "respiration_rate"))}</td><td>{bloodPressureText(day)}</td><td>{episodeText(day)}</td></tr>)}</tbody></table></div></div>}{displayedDays.length <= pageSize ? null : <nav className="pagination" aria-label="Daily pattern dates pagination"><p role="status">Showing {(pageStart + 1).toString()}–{Math.min(pageStart + pageSize, displayedDays.length).toString()} of {displayedDays.length.toString()} days. Page {currentPage.toString()} of {totalPages.toString()}.</p><div><button type="button" className="button-secondary" disabled={currentPage === 1} onClick={() => { setPage(currentPage - 1); }}>Previous</button><button type="button" className="button-secondary" disabled={currentPage === totalPages} onClick={() => { setPage(currentPage + 1); }}>Next</button></div></nav>}</section>
    <details className="metric-definition"><summary>Definitions, symptom timing, and technical provenance</summary>{Object.entries(data.definitions).map(([name, definition]) => <p key={name}><strong>{name.replaceAll("_", " ")}:</strong> {definition}</p>)}{data.days.flatMap((day) => day.symptom_timings.map((symptom) => <p key={symptom.symptom_event_id}><strong>{day.date} · {symptom.name}:</strong> severity {symptom.severity == null ? "missing" : `${symptom.severity.toString()}/10`}; {symptom.minutes_since_previous_supported_dose == null ? "no supported preceding dose in the model horizon" : `${formatDecimal(symptom.minutes_since_previous_supported_dose)} minutes since the latest supported dose`}; theoretical exposure {formatMeasurement(symptom.theoretical_exposure_reu, "REU")}.</p>))}{data.days.map((day) => <p key={`${day.date}-revision`}><strong>{day.date} technical source:</strong> features {day.feature_version}; exposure {day.exposure_model_version}; revision <code className="revision-watermark">{day.source_revision_watermark_sha256}</code>.</p>)}</details>
  </section>;
}
