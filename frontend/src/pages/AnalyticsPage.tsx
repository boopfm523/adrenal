import { useQuery } from "@tanstack/react-query";
import { useState, type PropsWithChildren } from "react";

import { getAnalyticsSummary, type AnalyticsSummary } from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { AccessibleLineChart } from "../components/AccessibleLineChart";
import { localDate } from "../time";

interface MetricFrameProps extends PropsWithChildren {
  title: string;
  metric: { definition: string; timezone: string; sample_count: number; missing_count: number };
}

function MetricFrame({ title, metric, children }: MetricFrameProps): React.JSX.Element {
  return <section className="metric-card"><h2>{title}</h2><dl className="metric-metadata"><div><dt>Timezone</dt><dd>{metric.timezone}</dd></div><div><dt>Sample count</dt><dd>{metric.sample_count}</dd></div><div><dt>Missing values</dt><dd>{metric.missing_count}</dd></div></dl>{children}<details className="metric-definition"><summary>Metric definition</summary><p>{metric.definition}</p></details></section>;
}

type DoseDay = AnalyticsSummary["daily_doses"]["values"][number];

function doseTotal(day: DoseDay, kind: "planned" | "actual"): string {
  if (day.incompatible_units) return "Unavailable—incompatible units";
  const value = kind === "planned" ? day.planned_total : day.actual_total;
  if (value === null) return kind === "planned" ? "Missing—no approved plan" : "Missing—no dose facts";
  return `${value} ${day.unit ?? "unit not recorded"}`;
}

function DailyDoses({ summary }: { summary: AnalyticsSummary }): React.JSX.Element {
  const metric = summary.daily_doses;
  const units = new Set(metric.values.map((day) => day.unit).filter((unit) => unit !== null));
  const chartUnit = units.size === 1 ? [...units][0] ?? "unit unavailable" : "mixed or unavailable units";
  return <><AccessibleLineChart title="Daily medication totals versus plan" summary={`${metric.days_without_approved_plan.toString()} day(s) have no physician-approved plan in force. Gaps mean the value is missing or unavailable.`} unit={chartUnit} timezone={metric.timezone} dateRange={`${summary.date_from} through ${summary.date_to}`} definition={metric.definition} sampleCount={metric.sample_count} missingCount={metric.missing_count} xAxisLabel="Date" yAxisLabel="Daily dose total" includeZero series={[{ name: "Physician-approved plan total", source: "approved regimen versions", values: metric.values.map((day) => ({ label: day.date, value: day.incompatible_units ? null : day.planned_total, unit: day.unit })) }, { name: "Recorded actual total", source: "current dose facts", values: metric.values.map((day) => ({ label: day.date, value: day.incompatible_units ? null : day.actual_total, unit: day.unit })) }]} /><div className="visually-hidden" aria-live="polite">{metric.values.map((day) => `${day.date}: planned ${doseTotal(day, "planned")}; actual ${doseTotal(day, "actual")}.`).join(" ")}</div></>;
}

function Timing({ summary }: { summary: AnalyticsSummary }): React.JSX.Element {
  const metric = summary.timing;
  return <MetricFrame title="Dose timing" metric={metric}><dl className="metric-values"><div><dt>On time</dt><dd>{metric.on_time}</dd></div><div><dt>Early</dt><dd>{metric.early}</dd></div><div><dt>Late</dt><dd>{metric.late}</dd></div><div><dt>Unplanned</dt><dd>{metric.unplanned}</dd></div><div><dt>Missing schedule matches</dt><dd>{metric.missing_count}</dd></div></dl></MetricFrame>;
}

function Episodes({ summary }: { summary: AnalyticsSummary }): React.JSX.Element {
  const metric = summary.episodes;
  return <MetricFrame title="Stress episodes" metric={metric}><dl className="metric-values"><div><dt>Episodes started</dt><dd>{metric.count}</dd></div><div><dt>Total resolved duration</dt><dd>{metric.sample_count === metric.missing_count ? "Missing—no resolved durations" : `${metric.total_duration_minutes} minutes`}</dd></div><div><dt>Average resolved duration</dt><dd>{metric.average_duration_minutes === null ? "Missing—no resolved durations" : `${metric.average_duration_minutes} minutes`}</dd></div><div><dt>Open episodes without duration</dt><dd>{metric.missing_count}</dd></div></dl></MetricFrame>;
}

function Symptoms({ summary }: { summary: AnalyticsSummary }): React.JSX.Element {
  const metric = summary.symptoms;
  return <MetricFrame title="Symptoms" metric={metric}><dl className="metric-values"><div><dt>Symptom facts</dt><dd>{metric.count}</dd></div><div><dt>Average recorded severity</dt><dd>{metric.average_severity === null ? "Missing—no severity values" : `${metric.average_severity} on the recorded 0-10 scale`}</dd></div><div><dt>Symptoms without severity</dt><dd>{metric.missing_count}</dd></div></dl><h3>Frequency by recorded name</h3>{Object.keys(metric.frequency).length === 0 ? <p>No symptom facts in this range.</p> : <ul>{Object.entries(metric.frequency).map(([name, count]) => <li key={name}>{name}: {count}</li>)}</ul>}</MetricFrame>;
}

export function AnalyticsPage(): React.JSX.Element {
  const { session } = useAuth();
  const profileTimezone = session?.user.defaultTimezone ?? "UTC";
  const [draft, setDraft] = useState(() => {
    const now = new Date();
    return { dateFrom: localDate(new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000), profileTimezone), dateTo: localDate(now, profileTimezone), timezone: profileTimezone };
  });
  const [filters, setFilters] = useState(draft);
  const summary = useQuery({ queryKey: ["analytics", filters], queryFn: () => getAnalyticsSummary(filters.dateFrom, filters.dateTo, filters.timezone) });

  return <Page title="Analytics" description="Deterministic summaries of recorded facts and approved plan data, with definitions and missingness shown.">
    <aside className="safety-note"><strong>Association does not establish causation.</strong> These summaries describe the selected records. They do not determine why a symptom, dose, or episode occurred and are not medical advice.</aside>
    <form className="filter-panel" onSubmit={(event) => { event.preventDefault(); setFilters(draft); }}><label>From date<input required type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /></label><label>Through date<input required type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /></label><label>IANA timezone<input required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></label><button type="submit">Calculate metrics</button></form>
    {summary.isPending ? <p role="status">Calculating deterministic metrics…</p> : null}{summary.isError ? <p className="error-summary" role="alert">Metrics could not be calculated. Check the date range and IANA timezone.</p> : null}
    {summary.data === undefined ? null : <><p className="analytics-range">Results for <strong>{summary.data.date_from}</strong> through <strong>{summary.data.date_to}</strong> in <strong>{summary.data.timezone}</strong>.</p><DailyDoses summary={summary.data} /><Timing summary={summary.data} /><Episodes summary={summary.data} /><Symptoms summary={summary.data} /></>}
  </Page>;
}
