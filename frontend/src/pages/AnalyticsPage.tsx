import { Alert, Button, Group, NativeSelect, Paper, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type PropsWithChildren } from "react";
import { useSearchParams } from "react-router-dom";

import {
  getAnalyticsSummary,
  getDailyPatterns,
  getDailyBloodPressure,
  getDailyEpisodes,
  getDailyGarminContext,
  getDailySymptoms,
  getDailyTemperature,
  getSteroidExposure,
  type AnalyticsSummary,
  type HealthCurveModel,
} from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { AccessibleLineChart } from "../components/AccessibleLineChart";
import { DayAnalysisCard } from "../components/DayAnalysisCard";
import {
  DailyHealthCurve,
  type HealthCurveVisibility,
} from "../components/DailyHealthCurve";
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

function selectedHealthCurveDay(
  searchParams: URLSearchParams,
  profileTimezone: string,
): { day: string; timezone: string; model: HealthCurveModel } {
  const requestedDay = searchParams.get("day");
  const requestedTimezone = searchParams.get("timezone")?.trim();
  const requestedModel = searchParams.get("model");
  return {
    day: requestedDay !== null && /^\d{4}-\d{2}-\d{2}$/.test(requestedDay)
      ? requestedDay
      : localDate(new Date(), profileTimezone),
    timezone: requestedTimezone === undefined || requestedTimezone === ""
      ? profileTimezone
      : requestedTimezone,
    model: requestedModel === "hc-exposure-v1" || requestedModel === "hc-physiology-v2" || requestedModel === "hc-wake-free-v3" || requestedModel === "hc-mixed-route-free-v4"
      ? requestedModel
      : "hc-mixed-route-free-v4",
  };
}

function HealthCurveDateFilter({
  selected,
  profileTimezone,
  onReview,
}: {
  selected: { day: string; timezone: string; model: HealthCurveModel };
  profileTimezone: string;
  onReview: (next: { day: string; timezone: string; model: HealthCurveModel }) => void;
}): React.JSX.Element {
  const [draft, setDraft] = useState(selected);
  const shortcuts = recentDayShortcuts(draft.timezone, profileTimezone);
  return <Paper component="form" className="healthcurve-date-filter" withBorder radius="lg" p="lg" onSubmit={(event) => { event.preventDefault(); onReview(draft); }}>
    <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
      <TextInput label="HealthCurve date" aria-label="HealthCurve date" required type="date" value={draft.day} onChange={(event) => { setDraft({ ...draft, day: event.target.value }); }} />
      <TextInput label="IANA timezone" aria-label="IANA timezone" required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} />
      <NativeSelect label="Exposure model" aria-label="Exposure model" required value={draft.model} data={[{ value: "hc-exposure-v1", label: "Simple relative exposure (v1)" }, { value: "hc-physiology-v2", label: "Physiological free-cortisol scenario (v2)" }, { value: "hc-wake-free-v3", label: "Wake-anchored oral free cortisol (v3)" }, { value: "hc-mixed-route-free-v4", label: "Full cortisol model (v4)" }]} onChange={(event) => { const value = event.currentTarget.value; if (value === "hc-exposure-v1" || value === "hc-physiology-v2" || value === "hc-wake-free-v3" || value === "hc-mixed-route-free-v4") setDraft({ ...draft, model: value }); }} />
    </SimpleGrid>
    <Group mt="md" justify="space-between" align="center">
      <Group gap="xs" role="group" aria-label="Quick HealthCurve dates"><Text fw={750}>Quick dates:</Text>{shortcuts.map((shortcut) => <Button key={shortcut.label} type="button" variant={selected.day === shortcut.day && selected.timezone === draft.timezone && selected.model === draft.model ? "filled" : "outline"} aria-pressed={selected.day === shortcut.day && selected.timezone === draft.timezone && selected.model === draft.model} onClick={() => { onReview({ day: shortcut.day, timezone: draft.timezone, model: draft.model }); }}>{shortcut.label}</Button>)}</Group>
      <Button type="submit">Review this day</Button>
    </Group>
  </Paper>;
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
  const [calculationRequest, setCalculationRequest] = useState(0);
  const calculationStatusRef = useRef<HTMLDivElement>(null);
  const dayFilter = selectedHealthCurveDay(searchParams, profileTimezone);
  const [curveVisibility, setCurveVisibility] = useState<HealthCurveVisibility>({
    exposure: true,
    stress: true,
    heart_rate: false,
    hrv: false,
    respiration_rate: false,
    steps: false,
    blood_pressure: false,
    temperature: false,
    symptoms: false,
    episodes: false,
  });
  const todayForSelectedTimezone = recentDayShortcuts(dayFilter.timezone, profileTimezone)[0]?.day
    ?? localDate(new Date(), profileTimezone);
  const dailyCurve = useQuery({
    queryKey: ["daily-healthcurve", dayFilter],
    queryFn: async () => {
      const [exposure, garmin, symptoms, bloodPressure, temperature, episodes] = await Promise.all([
        getSteroidExposure(dayFilter.day, dayFilter.timezone, dayFilter.model),
        getDailyGarminContext(dayFilter.day, dayFilter.timezone),
        getDailySymptoms(dayFilter.day, dayFilter.timezone),
        getDailyBloodPressure(dayFilter.day, dayFilter.timezone),
        getDailyTemperature(dayFilter.day, dayFilter.timezone),
        getDailyEpisodes(dayFilter.day, dayFilter.timezone),
      ]);
      return { exposure, garmin, symptoms, bloodPressure, temperature, episodes };
    },
    refetchInterval: 60_000,
  });
  const summary = useQuery({ queryKey: ["analytics", filters, calculationRequest], queryFn: () => getAnalyticsSummary(filters.dateFrom, filters.dateTo, filters.timezone) });
  const patterns = useQuery({ queryKey: ["daily-patterns", filters, calculationRequest], queryFn: () => getDailyPatterns(filters.dateFrom, filters.dateTo, filters.timezone) });
  const calculationPending = summary.isFetching || patterns.isFetching;
  const calculationFailed = summary.isError || patterns.isError;

  useEffect(() => {
    if (calculationRequest > 0 && !calculationPending) calculationStatusRef.current?.focus();
  }, [calculationPending, calculationRequest]);

  function reviewDay(next: { day: string; timezone: string; model: HealthCurveModel }): void {
    const search = new URLSearchParams(searchParams);
    search.set("day", next.day);
    search.set("timezone", next.timezone);
    search.set("model", next.model);
    setSearchParams(search);
  }

  function reviewAdjacentDay(offset: -1 | 1): void {
    const nextDay = shiftIsoDate(dayFilter.day, offset);
    if (offset === 1 && nextDay > todayForSelectedTimezone) return;
    reviewDay({ day: nextDay, timezone: dayFilter.timezone, model: dayFilter.model });
  }

  return <Page title="Daily review" documentTitle="HealthCurve.ai" description="Review one day from actual recorded doses and health context, then inspect longer-range deterministic summaries.">
    <HealthCurveDateFilter key={`${dayFilter.day}-${dayFilter.timezone}-${dayFilter.model}`} selected={dayFilter} profileTimezone={profileTimezone} onReview={reviewDay} />
    {dailyCurve.isPending ? <Text role="status" mt="md">Building your daily HealthCurve…</Text> : null}
    {dailyCurve.isError ? <Alert color="red" mt="md" role="alert">The daily HealthCurve could not be loaded. Check the selected date, IANA timezone, and exposure model.</Alert> : null}
    {dailyCurve.data === undefined ? null : <DailyHealthCurve data={dailyCurve.data} visible={curveVisibility} onVisibleChange={setCurveVisibility} onPreviousDay={() => { reviewAdjacentDay(-1); }} onNextDay={() => { reviewAdjacentDay(1); }} nextDayDisabled={dayFilter.day >= todayForSelectedTimezone} />}
    {dailyCurve.data === undefined ? null : <DayAnalysisCard day={dayFilter.day} timezone={dayFilter.timezone} />}
    <Stack className="analytics-history" gap="xs"><Title order={2} id="analytics-history-title">Longer-range analytics</Title><Text>Use these deterministic totals to compare days across a longer period. Daily pattern analysis builds on the selected-day HealthCurve.</Text></Stack>
    <Paper component="form" withBorder radius="lg" p="lg" mt="md" onSubmit={(event) => { event.preventDefault(); setFilters({ ...draft }); setCalculationRequest((request) => request + 1); }}><SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md"><TextInput label="From date" aria-label="From date" required type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /><TextInput label="Through date" aria-label="Through date" required type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /><TextInput label="IANA timezone" aria-label="IANA timezone" required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></SimpleGrid><Button type="submit" mt="md" loading={calculationRequest > 0 && calculationPending}>Calculate metrics</Button>
      {calculationRequest === 0 ? null : <div ref={calculationStatusRef} tabIndex={-1}>{calculationPending ? <Text role="status" mt="md">Calculating longer-range metrics…</Text> : calculationFailed ? <Alert color="red" mt="md" role="alert">Longer-range metrics could not be calculated. Check that the From date is on or before the Through date and that the IANA timezone is valid, then try again.</Alert> : <Alert color="teal" mt="md" role="status">Longer-range metrics updated for {filters.dateFrom} through {filters.dateTo}. Results appear below.</Alert>}</div>}
    </Paper>
    {patterns.isPending ? <p role="status">Deriving comparable daily features…</p> : null}{patterns.isError ? <p className="error-summary" role="alert">Daily pattern features could not be calculated. Check the date range and IANA timezone.</p> : null}
    {patterns.data === undefined ? null : <DailyPatternsTable data={patterns.data} />}
    {summary.isPending ? <p role="status">Calculating deterministic metrics…</p> : null}{summary.isError ? <p className="error-summary" role="alert">Metrics could not be calculated. Check the date range and IANA timezone.</p> : null}
    {summary.data === undefined ? null : <><p className="analytics-range">Results for <strong>{summary.data.date_from}</strong> through <strong>{summary.data.date_to}</strong> in <strong>{timezoneAbbreviationForLocalDate(summary.data.timezone, summary.data.date_to)}</strong>.</p><DailyDoses summary={summary.data} /><Timing summary={summary.data} /><Episodes summary={summary.data} /><Symptoms summary={summary.data} /></>}
  </Page>;
}
