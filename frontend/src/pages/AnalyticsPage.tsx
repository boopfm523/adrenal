import { useQuery } from "@tanstack/react-query";
import { useState, type PropsWithChildren } from "react";
import { useSearchParams } from "react-router-dom";

import {
  getAnalyticsSummary,
  getDailyPatterns,
  getDailyBloodPressure,
  getDailyEpisodes,
  getDailyGarminContext,
  getDailySymptoms,
  getSteroidExposure,
  type AnalyticsSummary,
} from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { AccessibleLineChart } from "../components/AccessibleLineChart";
import { DailyHealthCurve } from "../components/DailyHealthCurve";
import { DailyPatternsTable } from "../components/DailyPatternsTable";
import { formatDecimal, formatMeasurement } from "../format";
import { localDate, shiftIsoDate, timezoneAbbreviationForLocalDate } from "../time";

interface MetricFrameProps extends PropsWithChildren {
  title: string;
  metric: { definition: string; timezone: string; sample_count: number; missing_count: number };
  referenceDate: string;
}

function MetricFrame({ title, metric, referenceDate, children }: MetricFrameProps): React.JSX.Element {
  return <section className="metric-card"><h2>{title}</h2><dl className="metric-metadata"><div><dt>Timezone</dt><dd>{timezoneAbbreviationForLocalDate(metric.timezone, referenceDate)}</dd></div><div><dt>Sample count</dt><dd>{formatDecimal(metric.sample_count)}</dd></div><div><dt>Missing values</dt><dd>{formatDecimal(metric.missing_count)}</dd></div></dl>{children}<details className="metric-definition"><summary>Metric definition</summary><p>{metric.definition}</p></details></section>;
}

type DoseDay = AnalyticsSummary["daily_doses"]["values"][number];

function doseTotal(day: DoseDay, kind: "planned" | "actual"): string {
  if (day.incompatible_units) return "Unavailable—incompatible units";
  const value = kind === "planned" ? day.planned_total : day.actual_total;
  if (value === null) return kind === "planned" ? "Missing—no approved plan" : "Missing—no dose facts";
  return formatMeasurement(value, day.unit);
}

function DailyDoses({ summary }: { summary: AnalyticsSummary }): React.JSX.Element {
  const metric = summary.daily_doses;
  const units = new Set(metric.values.map((day) => day.unit).filter((unit) => unit !== null));
  const chartUnit = units.size === 1 ? [...units][0] ?? "unit unavailable" : "mixed or unavailable units";
  return <><AccessibleLineChart title="Daily medication totals versus plan" summary={`${formatDecimal(metric.days_without_approved_plan)} day(s) have no physician-approved plan in force. Gaps mean the value is missing or unavailable.`} unit={chartUnit} timezone={metric.timezone} timezoneReferenceDate={summary.date_to} dateRange={`${summary.date_from} through ${summary.date_to}`} definition={metric.definition} sampleCount={metric.sample_count} missingCount={metric.missing_count} xAxisLabel="Date" yAxisLabel="Daily dose total" includeZero series={[{ name: "Physician-approved plan total", source: "approved regimen versions", values: metric.values.map((day) => ({ label: day.date, value: day.incompatible_units ? null : day.planned_total, unit: day.unit })) }, { name: "Recorded actual total", source: "current dose facts", values: metric.values.map((day) => ({ label: day.date, value: day.incompatible_units ? null : day.actual_total, unit: day.unit })) }]} /><div className="visually-hidden" aria-live="polite">{metric.values.map((day) => `${day.date}: planned ${doseTotal(day, "planned")}; actual ${doseTotal(day, "actual")}.`).join(" ")}</div></>;
}

function Timing({ summary }: { summary: AnalyticsSummary }): React.JSX.Element {
  const metric = summary.timing;
  return <MetricFrame title="Dose timing" metric={metric} referenceDate={summary.date_to}>
    <dl className="metric-values">
      <div><dt>Matched doses</dt><dd>{formatDecimal(metric.matched_count)}</dd></div>
      <div><dt>Average absolute difference from plan</dt><dd>{metric.average_absolute_deviation_minutes === null ? "Missing—no matched doses" : `${formatDecimal(metric.average_absolute_deviation_minutes)} minutes`}</dd></div>
      <div><dt>Total absolute difference from plan</dt><dd>{metric.total_absolute_deviation_minutes === null ? "Missing—no matched doses" : `${formatDecimal(metric.total_absolute_deviation_minutes)} minutes`}</dd></div>
      <div><dt>On time</dt><dd>{formatDecimal(metric.on_time)}</dd></div><div><dt>Early</dt><dd>{formatDecimal(metric.early)}</dd></div><div><dt>Late</dt><dd>{formatDecimal(metric.late)}</dd></div><div><dt>Unplanned</dt><dd>{formatDecimal(metric.unplanned)}</dd></div><div><dt>Missing schedule matches</dt><dd>{formatDecimal(metric.missing_count)}</dd></div>
    </dl>
    <h3>Results by historical plan period</h3>
    {metric.plan_periods.length === 0 ? <p>No scheduled or recorded dose timing rows in this range.</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label="Dose timing by historical plan period">
      <table className="vital-table"><caption>Each row uses the physician-approved plan effective at the recorded or scheduled time. Missing and unplanned doses are excluded from minute averages.</caption><thead><tr><th scope="col">Plan</th><th scope="col">Effective interval</th><th scope="col">Matched</th><th scope="col">Average absolute difference</th><th scope="col">On time</th><th scope="col">Early</th><th scope="col">Late</th><th scope="col">Missing</th><th scope="col">Unplanned</th></tr></thead><tbody>{metric.plan_periods.map((period, index) => <tr key={period.regimen_version_id ?? `no-plan-${index.toString()}`}><th scope="row">{period.regimen_version_label ?? "No physician-approved plan"}</th><td>{period.effective_from === null ? "No plan interval" : `${period.effective_from} through ${period.effective_to ?? "ongoing"}`}</td><td>{formatDecimal(period.matched_count)}</td><td>{period.average_absolute_deviation_minutes === null ? "Missing—no matched doses" : `${formatDecimal(period.average_absolute_deviation_minutes)} minutes`}</td><td>{formatDecimal(period.on_time)}</td><td>{formatDecimal(period.early)}</td><td>{formatDecimal(period.late)}</td><td>{formatDecimal(period.missing_count)}</td><td>{formatDecimal(period.unplanned)}</td></tr>)}</tbody></table>
    </div>}
  </MetricFrame>;
}

function Episodes({ summary }: { summary: AnalyticsSummary }): React.JSX.Element {
  const metric = summary.episodes;
  return <MetricFrame title="Stress episodes" metric={metric} referenceDate={summary.date_to}><dl className="metric-values"><div><dt>Episodes started</dt><dd>{formatDecimal(metric.count)}</dd></div><div><dt>Total resolved duration</dt><dd>{metric.sample_count === metric.missing_count ? "Missing—no resolved durations" : `${formatDecimal(metric.total_duration_minutes)} minutes`}</dd></div><div><dt>Average resolved duration</dt><dd>{metric.average_duration_minutes === null ? "Missing—no resolved durations" : `${formatDecimal(metric.average_duration_minutes)} minutes`}</dd></div><div><dt>Open episodes without duration</dt><dd>{formatDecimal(metric.missing_count)}</dd></div></dl></MetricFrame>;
}

function Symptoms({ summary }: { summary: AnalyticsSummary }): React.JSX.Element {
  const metric = summary.symptoms;
  return <MetricFrame title="Symptoms" metric={metric} referenceDate={summary.date_to}><dl className="metric-values"><div><dt>Symptom facts</dt><dd>{formatDecimal(metric.count)}</dd></div><div><dt>Average recorded severity</dt><dd>{metric.average_severity === null ? "Missing—no severity values" : `${formatDecimal(metric.average_severity)} on the recorded 0-10 scale`}</dd></div><div><dt>Symptoms without severity</dt><dd>{formatDecimal(metric.missing_count)}</dd></div></dl><h3>Frequency by recorded name</h3>{Object.keys(metric.frequency).length === 0 ? <p>No symptom facts in this range.</p> : <ul>{Object.entries(metric.frequency).map(([name, count]) => <li key={name}>{name}: {formatDecimal(count)}</li>)}</ul>}</MetricFrame>;
}

function recentDayShortcuts(timezone: string, fallbackTimezone: string): readonly { label: string; day: string }[] {
  let today: string;
  try {
    today = localDate(new Date(), timezone);
  } catch {
    today = localDate(new Date(), fallbackTimezone);
  }
  return [
    { label: "Today", day: today },
    { label: "Yesterday", day: shiftIsoDate(today, -1) },
    { label: "2 days ago", day: shiftIsoDate(today, -2) },
  ];
}

export function AnalyticsPage(): React.JSX.Element {
  const { session } = useAuth();
  const profileTimezone = session?.user.defaultTimezone ?? "UTC";
  const [searchParams, setSearchParams] = useSearchParams();
  const [draft, setDraft] = useState(() => {
    const now = new Date();
    return { dateFrom: localDate(new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000), profileTimezone), dateTo: localDate(now, profileTimezone), timezone: profileTimezone };
  });
  const [filters, setFilters] = useState(draft);
  const [dayDraft, setDayDraft] = useState(() => {
    const requestedDay = searchParams.get("day");
    const requestedTimezone = searchParams.get("timezone")?.trim();
    return {
      day: requestedDay !== null && /^\d{4}-\d{2}-\d{2}$/.test(requestedDay) ? requestedDay : localDate(new Date(), profileTimezone),
      timezone: requestedTimezone === undefined || requestedTimezone === "" ? profileTimezone : requestedTimezone,
    };
  });
  const [dayFilter, setDayFilter] = useState(dayDraft);
  const dayShortcuts = recentDayShortcuts(dayDraft.timezone, profileTimezone);
  const dailyCurve = useQuery({
    queryKey: ["daily-healthcurve", dayFilter],
    queryFn: async () => {
      const [exposure, garmin, symptoms, bloodPressure, episodes] = await Promise.all([
        getSteroidExposure(dayFilter.day, dayFilter.timezone),
        getDailyGarminContext(dayFilter.day, dayFilter.timezone),
        getDailySymptoms(dayFilter.day, dayFilter.timezone),
        getDailyBloodPressure(dayFilter.day, dayFilter.timezone),
        getDailyEpisodes(dayFilter.day, dayFilter.timezone),
      ]);
      return { exposure, garmin, symptoms, bloodPressure, episodes };
    },
    refetchInterval: 60_000,
  });
  const summary = useQuery({ queryKey: ["analytics", filters], queryFn: () => getAnalyticsSummary(filters.dateFrom, filters.dateTo, filters.timezone) });
  const patterns = useQuery({ queryKey: ["daily-patterns", filters], queryFn: () => getDailyPatterns(filters.dateFrom, filters.dateTo, filters.timezone) });

  function reviewDay(next: { day: string; timezone: string }): void {
    setDayDraft(next);
    setDayFilter(next);
    const search = new URLSearchParams(searchParams);
    search.set("day", next.day);
    search.set("timezone", next.timezone);
    setSearchParams(search, { replace: true });
  }

  return <Page title="HealthCurve.ai" description="Review one day from actual recorded doses and health context, then inspect longer-range deterministic summaries.">
    <aside className="safety-note"><strong>Association does not establish causation.</strong> These summaries describe the selected records. They do not determine why a symptom, dose, or episode occurred and are not medical advice.</aside>
    <form className="filter-panel healthcurve-date-filter" onSubmit={(event) => { event.preventDefault(); reviewDay(dayDraft); }}><label>HealthCurve date<input required type="date" value={dayDraft.day} onChange={(event) => { setDayDraft({ ...dayDraft, day: event.target.value }); }} /></label><label>IANA timezone<input required value={dayDraft.timezone} onChange={(event) => { setDayDraft({ ...dayDraft, timezone: event.target.value }); }} /></label><button type="submit">Review this day</button><div className="healthcurve-date-shortcuts" role="group" aria-label="Quick HealthCurve dates"><span>Quick dates:</span>{dayShortcuts.map((shortcut) => <button key={shortcut.label} type="button" className={dayFilter.day === shortcut.day && dayFilter.timezone === dayDraft.timezone ? undefined : "button-secondary"} aria-pressed={dayFilter.day === shortcut.day && dayFilter.timezone === dayDraft.timezone} onClick={() => { reviewDay({ day: shortcut.day, timezone: dayDraft.timezone }); }}>{shortcut.label}</button>)}</div></form>
    {dailyCurve.isPending ? <p role="status">Building your daily HealthCurve…</p> : null}
    {dailyCurve.isError ? <p className="error-summary" role="alert">The daily HealthCurve could not be loaded. Check the selected date and IANA timezone.</p> : null}
    {dailyCurve.data === undefined ? null : <DailyHealthCurve data={dailyCurve.data} />}
    <section className="analytics-history" aria-labelledby="analytics-history-title"><h2 id="analytics-history-title">Longer-range analytics</h2><p>Use these deterministic totals to compare days across a longer period. Daily pattern analysis builds on the selected-day HealthCurve.</p></section>
    <form className="filter-panel" onSubmit={(event) => { event.preventDefault(); setFilters(draft); }}><label>From date<input required type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /></label><label>Through date<input required type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /></label><label>IANA timezone<input required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></label><button type="submit">Calculate metrics</button></form>
    {patterns.isPending ? <p role="status">Deriving comparable daily features…</p> : null}{patterns.isError ? <p className="error-summary" role="alert">Daily pattern features could not be calculated. Check the date range and IANA timezone.</p> : null}
    {patterns.data === undefined ? null : <DailyPatternsTable data={patterns.data} />}
    {summary.isPending ? <p role="status">Calculating deterministic metrics…</p> : null}{summary.isError ? <p className="error-summary" role="alert">Metrics could not be calculated. Check the date range and IANA timezone.</p> : null}
    {summary.data === undefined ? null : <><p className="analytics-range">Results for <strong>{summary.data.date_from}</strong> through <strong>{summary.data.date_to}</strong> in <strong>{timezoneAbbreviationForLocalDate(summary.data.timezone, summary.data.date_to)}</strong>.</p><DailyDoses summary={summary.data} /><Timing summary={summary.data} /><Episodes summary={summary.data} /><Symptoms summary={summary.data} /></>}
  </Page>;
}
