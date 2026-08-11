import { useMemo, useState } from "react";

import type {
  BloodPressure,
  Episode,
  GarminRecord,
  SteroidExposureCurve,
  Symptom,
} from "../api/client";
import {
  formatDecimal,
  formatGarminDailyValue,
  formatMeasurement,
  garminMetricLabel,
} from "../format";
import { timezoneAbbreviation, timezoneAbbreviationForLocalDate } from "../time";

export interface DailyHealthCurveData {
  exposure: SteroidExposureCurve;
  garmin: GarminRecord[];
  symptoms: Symptom[];
  bloodPressure: BloodPressure[];
  episodes: Episode[];
}

type LaneKey = "exposure" | "stress" | "heart_rate" | "hrv" | "respiration_rate" | "blood_pressure" | "symptoms" | "episodes";

interface Point {
  time: string;
  value: number;
  label: string;
  source: string;
  cadenceSeconds?: number;
}

interface Lane {
  key: LaneKey;
  label: string;
  unit: string;
  points: Point[];
}

interface TableRow {
  time: string | null;
  series: string;
  value: string;
  source: string;
}

interface SymptomObservation {
  id: string;
  time: string;
  label: string;
  source: string;
}

const WIDTH = 960;
const HEIGHT = 430;
const LEFT = 74;
const RIGHT = 24;
const TOP = 30;
const BOTTOM = 70;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM;
const RESPIRATION_DISPLAY_MIN = 0;
const RESPIRATION_DISPLAY_MAX = 40;
const RESPIRATION_MEDIAN_RADIUS = 2;

const DEFAULT_VISIBLE: Record<LaneKey, boolean> = {
  exposure: true,
  stress: true,
  heart_rate: false,
  hrv: false,
  respiration_rate: false,
  blood_pressure: false,
  symptoms: false,
  episodes: false,
};

const FOCUS_PRESETS: readonly { label: string; keys: readonly LaneKey[] }[] = [
  { label: "Stress", keys: ["exposure", "stress"] },
  { label: "Heart rate", keys: ["exposure", "heart_rate"] },
  { label: "HRV", keys: ["exposure", "hrv"] },
  { label: "Respiration", keys: ["exposure", "respiration_rate"] },
  { label: "Blood pressure", keys: ["exposure", "blood_pressure"] },
  { label: "Recorded events", keys: ["exposure", "symptoms", "episodes"] },
  { label: "All series (busy)", keys: Object.keys(DEFAULT_VISIBLE) as LaneKey[] },
];

const EXPOSURE_REFERENCE_DETAILS: Readonly<Record<string, { label: string; use: string }>> = {
  "https://doi.org/10.1002/j.1552-4604.1991.tb01906.x": { label: "Derendorf et al. (1991)", use: "conventional oral hydrocortisone pharmacokinetics and the 1.7-hour elimination half-life" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC5963674/": { label: "Johnson et al. (2018)", use: "measured oral time-to-peak, bioavailability, and binding-aware variability" },
  "https://doi.org/10.1016/j.metabol.2017.02.005": { label: "Werumeus Buning et al. (2017)", use: "population pharmacokinetics and the large between-person variability motivating a relative rather than clinical-unit curve" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC4880116/": { label: "Endocrine Society guideline (2016)", use: "the short plasma half-life and the distinction between replacement practice and this non-clinical visualization" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC9231005/": { label: "Röhr et al. (2022)", use: "evidence that clinical-unit models require more complex protein-binding and absorption handling" },
};

const REQUIREMENT_EVIDENCE = [
  { href: "https://pubmed.ncbi.nlm.nih.gov/23506003/", label: "Boonen et al. (2013)", use: "critical illness can reduce cortisol metabolism rather than simply accelerate elimination" },
  { href: "https://pmc.ncbi.nlm.nih.gov/articles/PMC7241266/", label: "Prete et al. (2020)", use: "major-stress cortisol delivery differs by administration method and cannot be inferred from a consumer stress score" },
  { href: "https://pmc.ncbi.nlm.nih.gov/articles/PMC3813945/", label: "Lewis and Elder (2013)", use: "cortisol-binding globulin materially affects total and free cortisol interpretation" },
] as const;

function presetVisibility(keys: readonly LaneKey[]): Record<LaneKey, boolean> {
  const included = new Set(keys);
  return Object.fromEntries(
    (Object.keys(DEFAULT_VISIBLE) as LaneKey[]).map((key) => [key, included.has(key)]),
  ) as Record<LaneKey, boolean>;
}

function isPresetVisible(visible: Record<LaneKey, boolean>, keys: readonly LaneKey[]): boolean {
  const preset = presetVisibility(keys);
  return (Object.keys(DEFAULT_VISIBLE) as LaneKey[]).every((key) => visible[key] === preset[key]);
}

const METRIC_LANES: readonly { key: LaneKey; metric: string; label: string; unit: string }[] = [
  { key: "stress", metric: "stress", label: "Garmin stress", unit: "score (0–100)" },
  { key: "heart_rate", metric: "heart_rate", label: "Heart rate", unit: "bpm" },
  { key: "hrv", metric: "hrv", label: "HRV", unit: "ms" },
  { key: "respiration_rate", metric: "respiration_rate", label: "Respiration", unit: "breaths/min" },
];

function numeric(value: string | null | undefined): number | null {
  if (value == null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function experiencedTime(value: string, timezone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZoneName: "shortOffset",
  }).format(new Date(value));
}

function timeTicks(start: number, end: number, timezone: string): { time: number; label: string }[] {
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return Array.from({ length: 7 }, (_, index) => {
    const time = start + index / 6 * (end - start);
    return { time, label: formatter.format(new Date(time)) };
  });
}

function scale(values: number[]): { minimum: number; maximum: number } {
  if (values.length === 0) return { minimum: 0, maximum: 1 };
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  if (minimum === maximum) return { minimum: Math.min(0, minimum), maximum: maximum + Math.max(1, Math.abs(maximum) * 0.1) };
  return { minimum, maximum };
}

function xPosition(time: string, start: number, end: number): number {
  return LEFT + (Date.parse(time) - start) / Math.max(end - start, 1) * PLOT_WIDTH;
}

function relativeValue(lane: Lane, value: number): number {
  const bounds = lane.key === "stress" ? { minimum: 0, maximum: 100 }
    : lane.key === "symptoms" ? { minimum: 0, maximum: 10 }
      : lane.key === "respiration_rate" ? { minimum: RESPIRATION_DISPLAY_MIN, maximum: RESPIRATION_DISPLAY_MAX }
      : scale(lane.points.map((point) => point.value));
  const relative = (value - bounds.minimum) / Math.max(bounds.maximum - bounds.minimum, 1) * 100;
  return lane.key === "respiration_rate" ? Math.max(0, Math.min(100, relative)) : relative;
}

function yPosition(lane: Lane, value: number): number {
  return TOP + PLOT_HEIGHT - relativeValue(lane, value) / 100 * PLOT_HEIGHT;
}

function connectedSegments(lane: Lane): Point[][] {
  if (lane.key === "symptoms") return [];
  if (lane.key === "exposure") return lane.points.length > 1 ? [lane.points] : [];
  const segments: Point[][] = [];
  let current: Point[] = [];
  for (const point of lane.points) {
    const previous = current.at(-1);
    if (point.cadenceSeconds === undefined) {
      if (current.length > 1) segments.push(current);
      current = [];
      continue;
    }
    if (previous !== undefined) {
      const allowedGap = Math.max(previous.cadenceSeconds ?? 0, point.cadenceSeconds) * 1_500;
      if (Date.parse(point.time) - Date.parse(previous.time) > allowedGap) {
        if (current.length > 1) segments.push(current);
        current = [];
      }
    }
    current.push(point);
  }
  if (current.length > 1) segments.push(current);
  return segments;
}

function path(lane: Lane, points: Point[], start: number, end: number): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${xPosition(point.time, start, end).toFixed(2)} ${yPosition(lane, point.value).toFixed(2)}`).join(" ");
}

function medianSmoothed(points: Point[], radius: number): Point[] {
  return points.map((point, index) => {
    const values = points
      .slice(Math.max(0, index - radius), Math.min(points.length, index + radius + 1))
      .map((candidate) => candidate.value)
      .sort((left, right) => left - right);
    const middle = Math.floor(values.length / 2);
    const median = values.length % 2 === 0
      ? ((values[middle - 1] ?? point.value) + (values[middle] ?? point.value)) / 2
      : (values[middle] ?? point.value);
    return { ...point, value: median };
  });
}

function displaySegment(lane: Lane, points: Point[]): Point[] {
  return lane.key === "respiration_rate" ? medianSmoothed(points, RESPIRATION_MEDIAN_RADIUS) : points;
}

function metricLane(data: DailyHealthCurveData, definition: typeof METRIC_LANES[number]): Lane {
  const points = data.garmin.flatMap((record) => {
    if (record.kind !== "sample" || record.metric_type !== definition.metric) return [];
    const value = numeric(record.value);
    if (value === null) return [];
    return [{
      time: record.time.occurred_at,
      value,
      label: formatMeasurement(record.value, record.unit),
      source: `Garmin ${record.provenance.confirmation_state}${record.sample_interval_seconds == null ? "" : `; observed cadence ${record.sample_interval_seconds.toString()} seconds`}`,
      ...(record.sample_interval_seconds == null ? {} : { cadenceSeconds: record.sample_interval_seconds }),
    }];
  });
  return { key: definition.key, label: definition.label, unit: definition.unit, points };
}

function lanes(data: DailyHealthCurveData): Lane[] {
  const exposure: Lane = {
    key: "exposure",
    label: "Theoretical exposure",
    unit: "REU",
    points: data.exposure.samples.map((sample) => ({
      time: sample.occurred_at,
      value: Number(sample.theoretical_exposure_reu),
      label: formatMeasurement(sample.theoretical_exposure_reu, "REU"),
      source: data.exposure.model.version,
      cadenceSeconds: data.exposure.model.sample_interval_minutes * 60,
    })),
  };
  const metricSeries = METRIC_LANES.map((definition) => metricLane(data, definition));
  const heartRate = metricSeries.find((lane) => lane.key === "heart_rate");
  heartRate?.points.push(...data.bloodPressure.flatMap((record) => record.pulse_bpm == null ? [] : [{
    time: record.time.occurred_at,
    value: record.pulse_bpm,
    label: `Blood-pressure pulse: ${record.pulse_bpm.toString()} bpm`,
    source: `${record.provenance.source_type}; ${record.provenance.confirmation_state}`,
  }]));
  heartRate?.points.sort((left, right) => Date.parse(left.time) - Date.parse(right.time));
  const bloodPressure: Lane = {
    key: "blood_pressure",
    label: "Blood pressure",
    unit: "mmHg",
    points: data.bloodPressure.flatMap((record) => [
      { time: record.time.occurred_at, value: record.systolic_mmhg, label: `${record.systolic_mmhg.toString()}/${record.diastolic_mmhg.toString()} mmHg — systolic point: ${record.systolic_mmhg.toString()} mmHg`, source: `${record.provenance.source_type}; ${record.provenance.confirmation_state}` },
      { time: record.time.occurred_at, value: record.diastolic_mmhg, label: `${record.systolic_mmhg.toString()}/${record.diastolic_mmhg.toString()} mmHg — diastolic point: ${record.diastolic_mmhg.toString()} mmHg`, source: `${record.provenance.source_type}; ${record.provenance.confirmation_state}` },
    ]),
  };
  const symptoms: Lane = {
    key: "symptoms",
    label: "Symptoms",
    unit: "recorded severity 0–10",
    points: data.symptoms.flatMap((record) => record.severity == null ? [] : [{
      time: record.time.occurred_at,
      value: record.severity,
      label: `${record.name}: ${record.severity.toString()}/10`,
      source: `${record.provenance.source_type}; ${record.provenance.confirmation_state}`,
    }]),
  };
  return [exposure, ...metricSeries, bloodPressure, symptoms];
}

function nearestVisiblePoints(shownLanes: Lane[], cursorTime: number): { lane: Lane; point: Point }[] {
  return shownLanes.flatMap((lane) => {
    const nearest = lane.points.reduce<Point | null>((current, candidate) => {
      if (current === null) return candidate;
      return Math.abs(Date.parse(candidate.time) - cursorTime) < Math.abs(Date.parse(current.time) - cursorTime) ? candidate : current;
    }, null);
    if (nearest === null) return [];
    const tolerance = lane.key === "exposure"
      ? (nearest.cadenceSeconds ?? 300) * 500
      : (nearest.cadenceSeconds ?? 120) * 500;
    if (Math.abs(Date.parse(nearest.time) - cursorTime) > tolerance) return [];
    return lane.points
      .filter((point) => point.time === nearest.time)
      .map((point) => ({ lane, point }));
  });
}

function missingSeverityObservations(symptoms: Symptom[]): SymptomObservation[] {
  return symptoms.flatMap((symptom) => symptom.severity == null ? [{
    id: symptom.id,
    time: symptom.time.occurred_at,
    label: `${symptom.name}: severity missing`,
    source: `${symptom.provenance.source_type}; ${symptom.provenance.confirmation_state}`,
  }] : []);
}

function nearbySymptomObservations(
  observations: SymptomObservation[],
  cursorTime: number,
): SymptomObservation[] {
  return observations.filter(
    (observation) => Math.abs(Date.parse(observation.time) - cursorTime) <= 60_000,
  );
}

function tableRows(data: DailyHealthCurveData, visible: Record<LaneKey, boolean>): TableRow[] {
  const rows: TableRow[] = lanes(data).flatMap((lane) => visible[lane.key] ? lane.points.map((point) => ({
    time: point.time,
    series: lane.label,
    value: point.label,
    source: point.source,
  })) : []);
  rows.push(...data.garmin.flatMap((record) => {
    if (record.kind !== "daily") return [];
    const lane = METRIC_LANES.find((definition) => definition.metric === record.metric_type);
    if (lane === undefined || !visible[lane.key]) return [];
    return [{
      time: null,
      series: record.measurement_label ?? garminMetricLabel(record.metric_type),
      value: formatGarminDailyValue(record.metric_type, record.value, record.unit),
      source: `Garmin ${record.provenance.confirmation_state.replaceAll("_", " ")}; untimed ${record.aggregation?.replaceAll("_", " ") ?? "aggregate"}`,
    }];
  }));
  if (visible.episodes) {
    rows.push(...data.episodes.map((episode) => ({
      time: episode.started_at,
      series: "Stress episode",
      value: `${episode.trigger}; ${episode.severity ?? "severity missing"}; ${episode.status}`,
      source: "recorded fact",
    })));
  }
  if (visible.symptoms) {
    rows.push(...data.symptoms.flatMap((symptom) => symptom.severity == null ? [{
      time: symptom.time.occurred_at,
      series: "Symptoms",
      value: `${symptom.name}: severity missing`,
      source: `${symptom.provenance.source_type}; ${symptom.provenance.confirmation_state}`,
    }] : []));
  }
  if (visible.exposure) {
    rows.push(...data.exposure.dose_markers.map((dose) => ({
      time: dose.occurred_at,
      series: "Actual dose marker",
      value: `${dose.medication_name}: ${formatMeasurement(dose.amount, dose.unit)}${dose.supported ? "" : `; excluded: ${dose.exclusion_reason ?? "unsupported"}`}`,
      source: `${dose.source_type}; ${dose.confirmation_state}`,
    })));
  }
  rows.push(...data.garmin.flatMap((record) => {
    if (record.kind !== "sleep" || record.ended_at == null) return [];
    const source = `Garmin ${record.provenance.confirmation_state.replaceAll("_", " ")}`;
    const sleepRows: TableRow[] = [{
      time: record.time.occurred_at,
      series: "Sleep start",
      value: "Garmin-recorded sleep session started",
      source,
    }];
    sleepRows.push(...(record.sleep_intervals ?? []).map((interval) => ({
      time: interval.started_at,
      series: "Awake interval",
      value: `Awake through ${experiencedTime(interval.ended_at, data.exposure.timezone)}`,
      source,
    })));
    sleepRows.push({
      time: record.ended_at,
      series: "Wake / sleep end",
      value: "Garmin-recorded sleep session ended",
      source,
    });
    return sleepRows;
  }));
  return rows.sort((left, right) => {
    if (left.time === null && right.time !== null) return -1;
    if (left.time !== null && right.time === null) return 1;
    if (left.time === null || right.time === null) return left.series.localeCompare(right.series);
    return Date.parse(left.time) - Date.parse(right.time) || left.series.localeCompare(right.series);
  });
}

export function DailyHealthCurve({ data }: { data: DailyHealthCurveData }): React.JSX.Element {
  const [visible, setVisible] = useState(DEFAULT_VISIBLE);
  const start = Date.parse(data.exposure.day_start);
  const end = Date.parse(data.exposure.day_end);
  const [cursorMinute, setCursorMinute] = useState(0);
  const [hoveringChart, setHoveringChart] = useState(false);
  const cursorTime = Math.min(end, start + cursorMinute * 60_000);
  const allLanes = useMemo(() => lanes(data), [data]);
  const shownLanes = allLanes.filter((lane) => visible[lane.key]);
  const ticks = timeTicks(start, end, data.exposure.timezone);
  const rows = useMemo(() => tableRows(data, visible), [data, visible]);
  const dailyAggregates = data.garmin.filter((record) => {
    if (record.kind !== "daily") return false;
    const lane = METRIC_LANES.find((definition) => definition.metric === record.metric_type);
    return lane !== undefined && visible[lane.key];
  });
  const sleepRecords = data.garmin.filter((record) => record.kind === "sleep" && record.ended_at != null);
  const missingWakeTiming = sleepRecords.some(
    (record) => (record.awakenings ?? 0) > 0 && (record.sleep_intervals ?? []).length === 0,
  );
  const cursorPoints = nearestVisiblePoints(shownLanes, cursorTime);
  const unscoredSymptoms = useMemo(() => missingSeverityObservations(data.symptoms), [data.symptoms]);
  const cursorUnscoredSymptoms = visible.symptoms
    ? nearbySymptomObservations(unscoredSymptoms, cursorTime)
    : [];
  const cursorRows = [
    ...cursorPoints.flatMap(({ lane, point }, index, points) => {
      if (lane.key === "blood_pressure") {
        const firstPointForReading = points.findIndex(
          (candidate) => candidate.lane.key === lane.key && candidate.point.time === point.time,
        );
        if (index !== firstPointForReading) return [];
        return [{
          key: `${lane.key}-${point.time}`,
          series: lane.label,
          value: point.label.split(" — ")[0] ?? point.label,
        }];
      }
      return [{
        key: `${lane.key}-${point.time}-${point.label}`,
        series: lane.label,
        value: lane.key === "exposure" ? `${point.value.toFixed(3)} REU` : point.label,
      }];
    }),
    ...cursorUnscoredSymptoms.map((symptom) => ({
      key: `unscored-symptom-${symptom.id}`,
      series: "Symptoms",
      value: symptom.label,
    })),
  ];
  const cursorLabel = experiencedTime(new Date(cursorTime).toISOString(), data.exposure.timezone);
  const cursorX = LEFT + (cursorTime - start) / Math.max(end - start, 1) * PLOT_WIDTH;
  const tooltipWidth = 280;
  const tooltipX = Math.min(WIDTH - tooltipWidth - 8, Math.max(LEFT + 8, cursorX + (cursorX > WIDTH * 0.62 ? -tooltipWidth - 12 : 12)));
  const tooltipHeight = Math.min(PLOT_HEIGHT - 24, 52 + Math.max(1, cursorRows.length) * 26);

  function moveCursor(clientX: number, left: number, width: number): void {
    const ratio = Math.max(0, Math.min(1, (clientX - left) / Math.max(width, 1)));
    setCursorMinute(Math.round(ratio * (end - start) / 60_000));
  }

  return <section className="metric-card healthcurve-card" aria-labelledby="daily-healthcurve-title">
    <h2 id="daily-healthcurve-title">Your daily HealthCurve</h2>
    <p>{data.exposure.safety_label}</p>
    <aside className="association-caution"><strong>Focused comparison on one time axis.</strong> The graph starts with theoretical exposure and Garmin stress so the shape stays readable. Choose another focus below or opt into the deliberately busy all-series view. Every enabled series uses a relative 0–100 display scale. Exact values keep their original units in the hover tooltip and table. Relative heights are not equivalent measurements, do not establish causation, do not measure cortisol, and do not determine medication need.</aside>
    <dl className="metric-metadata"><div><dt>Selected date</dt><dd>{data.exposure.date}</dd></div><div><dt>Timezone</dt><dd>{timezoneAbbreviationForLocalDate(data.exposure.timezone, data.exposure.date)}</dd></div><div><dt>Elapsed day</dt><dd>{formatDecimal(data.exposure.elapsed_hours)} hours</dd></div><div><dt>Model</dt><dd>{data.exposure.model.version}</dd></div></dl>
    {dailyAggregates.length === 0 ? null : <section className="garmin-aggregate-context" aria-labelledby="garmin-aggregate-context-title"><h3 id="garmin-aggregate-context-title">Garmin aggregate context</h3><p>These values summarize a provider-defined period. They have no exact intraday observation time, so they are not positioned on or connected within the chart.</p><dl>{dailyAggregates.map((record) => <div key={record.id}><dt>{record.measurement_label ?? garminMetricLabel(record.metric_type)}</dt><dd><strong>{formatGarminDailyValue(record.metric_type, record.value, record.unit)}</strong><span>{record.period_label === null || record.period_label === undefined ? "Untimed aggregate" : `Untimed · ${record.period_label}`} · Garmin {record.provenance.confirmation_state.replaceAll("_", " ")}</span></dd></div>)}</dl></section>}
    <p className="curve-missingness"><strong>Missingness:</strong> Garmin cadence is observational, so expected missing counts are not invented. Lines connect only contiguous samples with an observed cadence. Unknown or interrupted intervals remain blank; no interpolated values are stored as facts.{missingWakeTiming ? " Garmin reported one or more awakenings without their exact times, so no intermediate wake markers are invented for those sessions." : ""}</p>
    <div className="healthcurve-controls" aria-label="HealthCurve chart controls"><div className="healthcurve-focus" role="group" aria-label="Choose a focused HealthCurve comparison"><span>Quick focus:</span>{FOCUS_PRESETS.map((preset) => <button key={preset.label} type="button" className={isPresetVisible(visible, preset.keys) ? undefined : "button-secondary"} aria-pressed={isPresetVisible(visible, preset.keys)} onClick={() => { setVisible(presetVisibility(preset.keys)); }}>{preset.label}</button>)}</div>
      <fieldset className="curve-toggles"><legend>Show or hide chart series</legend>{Object.entries({
        exposure: "Theoretical exposure and actual doses",
        stress: "Garmin stress",
        heart_rate: "Heart rate",
        hrv: "HRV",
        respiration_rate: "Respiration",
        blood_pressure: "Blood pressure",
        symptoms: "Symptoms",
        episodes: "Stress episodes",
      } satisfies Record<LaneKey, string>).map(([key, label]) => <label key={key} className="checkbox-label"><input type="checkbox" checked={visible[key as LaneKey]} onChange={(event) => { setVisible({ ...visible, [key]: event.target.checked }); }} />{label}</label>)}</fieldset></div>
    <div className="healthcurve-legend" aria-label="Overlay series legend">{sleepRecords.length === 0 ? null : <><span><i className="healthcurve-key healthcurve-key--sleep" aria-hidden="true" />Sleep session</span><span><i className="healthcurve-key healthcurve-key--awake" aria-hidden="true" />Explicit awake interval</span></>}{shownLanes.map((lane) => <span key={lane.key}><i className={`healthcurve-key healthcurve-key--${lane.key}`} aria-hidden="true" />{lane.label} · {lane.unit}{lane.key === "respiration_rate" ? " · calmer 5-sample median line" : ""}</span>)}</div>
    {visible.symptoms && data.symptoms.length > 0 ? <section className="healthcurve-recorded-symptoms" aria-labelledby="healthcurve-recorded-symptoms-title"><h3 id="healthcurve-recorded-symptoms-title">Recorded symptoms</h3><p>Symptoms without a recorded severity use a time marker below the numeric scale; HealthCurve does not treat missing severity as zero.</p><ul>{data.symptoms.map((symptom) => <li key={symptom.id}><time dateTime={symptom.time.occurred_at}>{experiencedTime(symptom.time.occurred_at, data.exposure.timezone)}</time>: <strong>{symptom.name}</strong> — {symptom.severity == null ? "severity not recorded" : `${symptom.severity.toString()}/10`}</li>)}</ul></section> : null}
    <div className="healthcurve-scroll" tabIndex={0} role="region" aria-label="Daily HealthCurve synchronized chart">
      <svg className="healthcurve-chart" viewBox={`0 0 ${WIDTH.toString()} ${HEIGHT.toString()}`} role="img" aria-label={`Interactive selected-day HealthCurve overlay for ${data.exposure.date} in ${timezoneAbbreviationForLocalDate(data.exposure.timezone, data.exposure.date)}; ${data.symptoms.length.toString()} recorded symptom ${data.symptoms.length === 1 ? "event" : "events"}; relative display positions share one time axis and exact values follow.`}>
        <rect className="healthcurve-overlay-bg" x={LEFT} y={TOP} width={PLOT_WIDTH} height={PLOT_HEIGHT} />
        {sleepRecords.length === 0 ? null : <g data-series="sleep">{sleepRecords.map((record) => {
          const sessionStart = Date.parse(record.time.occurred_at);
          const sessionEnd = Date.parse(record.ended_at ?? record.time.occurred_at);
          const clippedStart = Math.max(start, sessionStart);
          const clippedEnd = Math.min(end, sessionEnd);
          const startX = LEFT + (clippedStart - start) / Math.max(end - start, 1) * PLOT_WIDTH;
          const endX = LEFT + (clippedEnd - start) / Math.max(end - start, 1) * PLOT_WIDTH;
          return <g key={record.id} className="healthcurve-sleep-session">
            <title>{experiencedTime(record.time.occurred_at, data.exposure.timezone)} sleep start; {experiencedTime(record.ended_at ?? record.time.occurred_at, data.exposure.timezone)} wake / sleep end; Garmin {record.provenance.confirmation_state.replaceAll("_", " ")}</title>
            <rect className="healthcurve-sleep-band" x={startX} y={TOP} width={Math.max(2, endX - startX)} height="12" />
            {(record.sleep_intervals ?? []).map((interval, index) => {
              const awakeStart = Math.max(start, Date.parse(interval.started_at));
              const awakeEnd = Math.min(end, Date.parse(interval.ended_at));
              if (awakeEnd <= awakeStart) return null;
              const awakeX = LEFT + (awakeStart - start) / Math.max(end - start, 1) * PLOT_WIDTH;
              const awakeEndX = LEFT + (awakeEnd - start) / Math.max(end - start, 1) * PLOT_WIDTH;
              return <rect key={`${interval.started_at}-${index.toString()}`} className="healthcurve-awake-interval" x={awakeX} y={TOP} width={Math.max(2, awakeEndX - awakeX)} height={PLOT_HEIGHT}><title>{experiencedTime(interval.started_at, data.exposure.timezone)} through {experiencedTime(interval.ended_at, data.exposure.timezone)}: explicit Garmin awake interval</title></rect>;
            })}
            {sessionStart >= start && sessionStart < end ? <><line className="healthcurve-sleep-marker healthcurve-sleep-marker--start" x1={startX} y1={TOP} x2={startX} y2={TOP + PLOT_HEIGHT} /><text aria-hidden="true" className="healthcurve-sleep-label" x={startX + 4} y={TOP + 27}>Sleep start</text></> : null}
            {sessionEnd > start && sessionEnd <= end ? <><line className="healthcurve-sleep-marker healthcurve-sleep-marker--end" x1={endX} y1={TOP} x2={endX} y2={TOP + PLOT_HEIGHT} /><text aria-hidden="true" className="healthcurve-sleep-label" x={endX + 4} y={TOP + 27}>Wake</text></> : null}
          </g>;
        })}</g>}
        {[0, 25, 50, 75, 100].map((relative) => {
          const y = TOP + PLOT_HEIGHT - relative / 100 * PLOT_HEIGHT;
          return <g key={relative}><line className="healthcurve-relative-grid" x1={LEFT} y1={y} x2={LEFT + PLOT_WIDTH} y2={y} /><text className="healthcurve-scale-label" x={LEFT - 10} y={y} dy="0.35em" textAnchor="end">{relative.toString()}</text></g>;
        })}
        {ticks.map((tick, index) => {
          const x = LEFT + index / Math.max(ticks.length - 1, 1) * PLOT_WIDTH;
          return <g key={tick.time}><line className="healthcurve-time-grid" x1={x} y1={TOP} x2={x} y2={TOP + PLOT_HEIGHT} /><text className="healthcurve-time-label" x={x} y={HEIGHT - 34} textAnchor={index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle"}>{tick.label}</text></g>;
        })}
        {visible.episodes ? <g data-series="episodes">{data.episodes.map((episode) => {
            const x = Math.max(LEFT, Math.min(LEFT + PLOT_WIDTH, xPosition(episode.started_at, start, end)));
            const xEnd = Math.max(LEFT, Math.min(LEFT + PLOT_WIDTH, xPosition(episode.ended_at ?? data.exposure.day_end, start, end)));
            return <rect key={episode.id} className="healthcurve-episode" x={x} y={TOP} width={Math.max(4, xEnd - x)} height={PLOT_HEIGHT}><title>{experiencedTime(episode.started_at, data.exposure.timezone)}: {episode.trigger}; {episode.severity ?? "severity missing"}; {episode.status}</title></rect>;
          })}</g> : null}
        {shownLanes.map((lane) => <g key={lane.key} data-series={lane.key}>
          {connectedSegments(lane).map((segment, index) => <path key={`${lane.key}-${index.toString()}`} className={`healthcurve-series healthcurve-series--${lane.key}${lane.key === "exposure" ? " healthcurve-exposure-line" : ""}`} d={path(lane, displaySegment(lane, segment), start, end)} />)}
          {lane.key === "blood_pressure" ? data.bloodPressure.map((record) => {
            const x = xPosition(record.time.occurred_at, start, end);
            const systolicY = yPosition(lane, record.systolic_mmhg);
            const diastolicY = yPosition(lane, record.diastolic_mmhg);
            const pair = `${record.systolic_mmhg.toString()}/${record.diastolic_mmhg.toString()} mmHg`;
            const source = `${record.provenance.source_type}; ${record.provenance.confirmation_state}`;
            return <g key={record.id} className="healthcurve-blood-pressure-pair">
              <title>{experiencedTime(record.time.occurred_at, data.exposure.timezone)}: Blood pressure {pair}; source {source}</title>
              <line className="healthcurve-blood-pressure-link" x1={x} y1={systolicY} x2={x} y2={diastolicY} />
              <circle className="healthcurve-point healthcurve-point--blood_pressure healthcurve-point--systolic" cx={x} cy={systolicY} r="5" />
              <text aria-hidden="true" className="healthcurve-blood-pressure-label" x={x + 8} y={Math.max(TOP + 12, Math.min(TOP + PLOT_HEIGHT - 4, systolicY + 4))}>S</text>
              <circle className="healthcurve-point healthcurve-point--blood_pressure healthcurve-point--diastolic" cx={x} cy={diastolicY} r="5" />
              <text aria-hidden="true" className="healthcurve-blood-pressure-label" x={x + 8} y={Math.max(TOP + 12, Math.min(TOP + PLOT_HEIGHT - 4, diastolicY + 4))}>D</text>
            </g>;
          }) : null}
          {lane.key === "symptoms" ? lane.points.map((point, index) => <circle key={`${point.time}-${index.toString()}`} className={`healthcurve-point healthcurve-point--${lane.key}`} cx={xPosition(point.time, start, end)} cy={yPosition(lane, point.value)} r="6"><title>{experiencedTime(point.time, data.exposure.timezone)}: {point.label}; source {point.source}</title></circle>) : null}
        </g>)}
        {visible.exposure ? data.exposure.dose_markers.map((dose) => {
          const x = xPosition(dose.occurred_at, start, end);
          return <g key={dose.dose_event_id}><line className="healthcurve-dose-marker" x1={x} y1={TOP} x2={x} y2={TOP + PLOT_HEIGHT}><title>{experiencedTime(dose.occurred_at, data.exposure.timezone)}: actual dose {formatMeasurement(dose.amount, dose.unit)} {dose.medication_name}</title></line><circle className="healthcurve-dose-dot" cx={x} cy={TOP} r="5" /></g>;
        }) : null}
        {visible.symptoms ? <g data-series="unscored-symptoms">{unscoredSymptoms.map((symptom, index) => {
          const x = xPosition(symptom.time, start, end);
          const y = TOP + PLOT_HEIGHT + 10 + index % 2 * 10;
          return <g key={symptom.id} className="healthcurve-unscored-symptom"><title>{experiencedTime(symptom.time, data.exposure.timezone)}: {symptom.label}; source {symptom.source}; marker is outside the severity scale</title><line className="healthcurve-unscored-symptom-line" x1={x} y1={TOP} x2={x} y2={TOP + PLOT_HEIGHT} /><polygon className="healthcurve-unscored-symptom-marker" points={`${(x - 5).toString()},${y.toString()} ${x.toString()},${(y - 5).toString()} ${(x + 5).toString()},${y.toString()} ${x.toString()},${(y + 5).toString()}`} /></g>;
        })}</g> : null}
        <line className="healthcurve-cursor" x1={cursorX} y1={TOP} x2={cursorX} y2={TOP + PLOT_HEIGHT} />
        <rect className="healthcurve-pointer-target" x={LEFT} y={TOP} width={PLOT_WIDTH} height={PLOT_HEIGHT} onPointerEnter={() => { setHoveringChart(true); }} onPointerLeave={() => { setHoveringChart(false); }} onPointerMove={(event) => { const bounds = event.currentTarget.getBoundingClientRect(); moveCursor(event.clientX, bounds.left, bounds.width); }} />
        {hoveringChart ? <foreignObject className="healthcurve-hover-tooltip" x={tooltipX} y={TOP + 12} width={tooltipWidth} height={tooltipHeight}><div className="healthcurve-hover-tooltip-card" role="tooltip"><strong>{cursorLabel}</strong>{cursorRows.length === 0 ? <p>No exact observation at this time.</p> : <ul>{cursorRows.map((row) => <li key={`tooltip-${row.key}`}><strong>{row.series}:</strong> {row.value}</li>)}</ul>}</div></foreignObject> : null}
        <text transform={`translate(18 ${String(TOP + PLOT_HEIGHT / 2)}) rotate(-90)`} textAnchor="middle" className="healthcurve-axis-title">Relative display position (0–100)</text>
        <text x={LEFT + PLOT_WIDTH / 2} y={HEIGHT - 8} textAnchor="middle" className="healthcurve-axis-title">Local time ({timezoneAbbreviation(data.exposure.timezone, data.exposure.day_start)})</text>
      </svg>
    </div>
    <div className="curve-series-summary" aria-label="Visible series sample counts">{shownLanes.map((lane) => lane.key === "symptoms" ? <p key={lane.key}><strong>Symptoms:</strong> {data.symptoms.length.toString()} recorded {data.symptoms.length === 1 ? "event" : "events"}; {lane.points.length.toString()} with recorded severity; {unscoredSymptoms.length.toString()} without severity. Missing-severity markers sit outside the numeric scale.</p> : <p key={lane.key}><strong>{lane.label}:</strong> {lane.points.length.toString()} exact point(s); {lane.unit}; gaps remain missing.{lane.key === "respiration_rate" ? ` The line uses a display-only 5-sample rolling median on each contiguous segment and a fixed 0–${RESPIRATION_DISPLAY_MAX.toString()} breaths/min display domain; exact unsmoothed values remain in the tooltip and table.` : ["stress", "heart_rate", "hrv"].includes(lane.key) ? " Dense sample dots are hidden from the graph but every value remains in the tooltip and table." : ""}</p>)}</div>
    <details className="chart-table"><summary>View exact values and provenance</summary><div className="table-scroll" tabIndex={0} role="region" aria-label="Daily HealthCurve exact values"><table><caption>Current recorded facts and deterministic model samples shown in the selected lanes. Timestamped rows sort by experienced instant; aggregates are explicitly untimed.</caption><thead><tr><th scope="col">Local date / time or period</th><th scope="col">Series</th><th scope="col">Exact value</th><th scope="col">Source / provenance</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.time ?? "aggregate"}-${row.series}-${index.toString()}`}><th scope="row">{row.time === null ? `${data.exposure.date} · untimed aggregate` : experiencedTime(row.time, data.exposure.timezone)}</th><td>{row.series}</td><td>{row.value}</td><td>{row.source}</td></tr>)}</tbody></table></div></details>
    <details className="metric-definition model-methodology"><summary>How this model works: formulas, sources, and limits</summary>
      <p>{data.exposure.definition}</p>
      <h3>Exact implemented exposure formula</h3>
      <p><strong>{data.exposure.model.version}</strong> supports only {data.exposure.model.supported_formulation} {data.exposure.model.supported_route} {data.exposure.model.supported_medication} recorded in {data.exposure.model.amount_unit}. For elapsed hours <code>t</code> after each actual dose:</p>
      <pre><code>{`ka = ${formatDecimal(data.exposure.model.absorption_rate_per_hour)} per hour\nke = ln(2) / ${formatDecimal(data.exposure.model.elimination_half_life_hours)} hours = ${formatDecimal(data.exposure.model.elimination_rate_per_hour)} per hour\nt_peak = ln(ka / ke) / (ka - ke) = ${formatDecimal(data.exposure.model.peak_time_hours)} hours\nraw(t) = exp(-ke × t) - exp(-ka × t)\nshape(t) = raw(t) / raw(t_peak)\ndose_contribution(t) = recorded_amount_mg × shape(t) REU\ntotal_exposure(t) = sum of every supported current dose contribution`}</code></pre>
      <p>Each contribution is zero before its recorded administration and after {data.exposure.model.contribution_horizon_hours.toString()} hours. It rises from zero, reaches a normalized peak of 1 REU per recorded mg at <code>t_peak</code>, then declines. Contributions from close or simultaneous doses are summed; none resets another. Output is sampled every {data.exposure.model.sample_interval_minutes.toString()} elapsed minutes plus exact administration and modeled-peak knots. REU is a relative visualization unit, not nmol/L, µg/dL, biological effect, or medication adequacy.</p>
      <h3>Why these parameters are used</h3>
      <ul>{data.exposure.model.references.map((href) => { const detail = EXPOSURE_REFERENCE_DETAILS[href]; return <li key={href}><a href={href} target="_blank" rel="noreferrer">{detail?.label ?? "Model source"}</a>{detail === undefined ? null : ` — ${detail.use}.`}</li>; })}</ul>
      <h3>No “needed cortisol” formula is active</h3>
      <p>HealthCurve currently calculates no baseline, Garmin-stress-derived, or symptom-derived cortisol “needed” value. The supplied exploratory scenario used <code>Req(t) = Base(t) × S(t)</code>, but its population baseline anchors and stress multipliers are not part of {data.exposure.model.version}. Garmin stress remains a provider score on its own scale. Symptoms retain their recorded 0–10 severity and use <code>severity × 10</code> only for display position. Missing values remain missing. None of these inputs changes the exposure curve or becomes a dose multiplier, coverage ratio, or physiological requirement.</p>
      <p>That boundary exists because the available evidence describes hydrocortisone pharmacokinetics and stress physiology but does not validate a minute-by-minute conversion from Garmin stress or subjective symptoms to an individual cortisol requirement:</p>
      <ul>{REQUIREMENT_EVIDENCE.map((source) => <li key={source.href}><a href={source.href} target="_blank" rel="noreferrer">{source.label}</a>{` — ${source.use}.`}</li>)}</ul>
      <h3>Overlay display formula</h3>
      <p>For every non-stress, non-symptom, non-respiration numeric lane, let <code>display_min</code> and <code>display_max</code> be its observed selected-day minimum and maximum. The graph uses <code>display = 100 × (value - display_min) / max(display_max - display_min, 1)</code>. An empty lane uses bounds 0 and 1. If every point equals <code>v</code>, the fallback bounds are <code>min(0, v)</code> and <code>v + max(1, abs(v) × 0.1)</code>. Garmin stress uses fixed bounds 0 and 100. Symptoms use fixed bounds 0 and 10, equivalent to <code>severity × 10</code>. Respiration uses a fixed 0–{RESPIRATION_DISPLAY_MAX.toString()} breaths/min display domain and a centered 5-sample median within each observed contiguous segment; values outside the display domain are clipped on the graph only. This changes only screen position; exact native values remain in the tooltip and table. Relative heights are not equivalent measurements and do not establish cortisol need, correlation, or causation.</p>
    </details>
  </section>;
}
