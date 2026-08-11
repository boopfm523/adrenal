import { useMemo, useState } from "react";

import type {
  BloodPressure,
  Episode,
  GarminRecord,
  SteroidExposureCurve,
  Symptom,
} from "../api/client";
import { formatDecimal, formatMeasurement } from "../format";

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
  time: string;
  series: string;
  value: string;
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

const DEFAULT_VISIBLE: Record<LaneKey, boolean> = {
  exposure: true,
  stress: true,
  heart_rate: true,
  hrv: true,
  respiration_rate: true,
  blood_pressure: true,
  symptoms: true,
  episodes: true,
};

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
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "shortOffset",
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
      : scale(lane.points.map((point) => point.value));
  return (value - bounds.minimum) / Math.max(bounds.maximum - bounds.minimum, 1) * 100;
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

function metricLane(data: DailyHealthCurveData, definition: typeof METRIC_LANES[number]): Lane {
  const points = data.garmin.flatMap((record) => {
    if (record.metric_type !== definition.metric) return [];
    const value = numeric(record.value);
    if (value === null) return [];
    return [{
      time: record.time.occurred_at,
      value,
      label: `${definition.label}: ${formatMeasurement(record.value, record.unit)}`,
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
      label: `Theoretical exposure: ${formatMeasurement(sample.theoretical_exposure_reu, "REU")}`,
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
      { time: record.time.occurred_at, value: record.systolic_mmhg, label: `Systolic: ${record.systolic_mmhg.toString()} mmHg`, source: `${record.provenance.source_type}; ${record.provenance.confirmation_state}` },
      { time: record.time.occurred_at, value: record.diastolic_mmhg, label: `Diastolic: ${record.diastolic_mmhg.toString()} mmHg`, source: `${record.provenance.source_type}; ${record.provenance.confirmation_state}` },
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
    const point = lane.points.reduce<Point | null>((nearest, candidate) => {
      if (nearest === null) return candidate;
      return Math.abs(Date.parse(candidate.time) - cursorTime) < Math.abs(Date.parse(nearest.time) - cursorTime) ? candidate : nearest;
    }, null);
    if (point === null) return [];
    const tolerance = lane.key === "exposure"
      ? (point.cadenceSeconds ?? 300) * 500
      : (point.cadenceSeconds ?? 120) * 500;
    return Math.abs(Date.parse(point.time) - cursorTime) <= tolerance ? [{ lane, point }] : [];
  });
}

function tableRows(data: DailyHealthCurveData, visible: Record<LaneKey, boolean>): TableRow[] {
  const rows = lanes(data).flatMap((lane) => visible[lane.key] ? lane.points.map((point) => ({
    time: point.time,
    series: lane.label,
    value: point.label,
    source: point.source,
  })) : []);
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
  return rows.sort((left, right) => Date.parse(left.time) - Date.parse(right.time) || left.series.localeCompare(right.series));
}

export function DailyHealthCurve({ data }: { data: DailyHealthCurveData }): React.JSX.Element {
  const [visible, setVisible] = useState(DEFAULT_VISIBLE);
  const start = Date.parse(data.exposure.day_start);
  const end = Date.parse(data.exposure.day_end);
  const [cursorMinute, setCursorMinute] = useState(0);
  const cursorTime = Math.min(end, start + cursorMinute * 60_000);
  const allLanes = useMemo(() => lanes(data), [data]);
  const shownLanes = allLanes.filter((lane) => visible[lane.key]);
  const ticks = timeTicks(start, end, data.exposure.timezone);
  const rows = useMemo(() => tableRows(data, visible), [data, visible]);
  const cursorPoints = nearestVisiblePoints(shownLanes, cursorTime);
  const elapsedMinutes = Math.round((end - start) / 60_000);
  const cursorX = LEFT + (cursorTime - start) / Math.max(end - start, 1) * PLOT_WIDTH;

  function moveCursor(clientX: number, left: number, width: number): void {
    const ratio = Math.max(0, Math.min(1, (clientX - left) / Math.max(width, 1)));
    setCursorMinute(Math.round(ratio * (end - start) / 60_000));
  }

  return <section className="metric-card healthcurve-card" aria-labelledby="daily-healthcurve-title">
    <h2 id="daily-healthcurve-title">Your daily HealthCurve</h2>
    <p>{data.exposure.safety_label}</p>
    <aside className="association-caution"><strong>One time axis, relative display scales.</strong> Every enabled series is overlaid on a relative 0–100 display scale so its shape can be compared in time. Exact values keep their original units in the interactive readout and table. Relative heights are not equivalent measurements, do not establish causation, do not measure cortisol, and do not determine medication need.</aside>
    <fieldset className="curve-toggles"><legend>Show or hide chart series</legend>{Object.entries({
      exposure: "Theoretical exposure and actual doses",
      stress: "Garmin stress",
      heart_rate: "Heart rate",
      hrv: "HRV",
      respiration_rate: "Respiration",
      blood_pressure: "Blood pressure",
      symptoms: "Symptoms",
      episodes: "Stress episodes",
    } satisfies Record<LaneKey, string>).map(([key, label]) => <label key={key} className="checkbox-label"><input type="checkbox" checked={visible[key as LaneKey]} onChange={(event) => { setVisible({ ...visible, [key]: event.target.checked }); }} />{label}</label>)}</fieldset>
    <dl className="metric-metadata"><div><dt>Selected date</dt><dd>{data.exposure.date}</dd></div><div><dt>Timezone</dt><dd>{data.exposure.timezone}</dd></div><div><dt>Elapsed day</dt><dd>{formatDecimal(data.exposure.elapsed_hours)} hours</dd></div><div><dt>Model</dt><dd>{data.exposure.model.version}</dd></div></dl>
    <p className="curve-missingness"><strong>Missingness:</strong> Garmin cadence is observational, so expected missing counts are not invented. Lines connect only contiguous samples with an observed cadence. Unknown or interrupted intervals remain blank; no interpolated values are stored as facts.</p>
    <div className="healthcurve-legend" aria-label="Overlay series legend">{shownLanes.map((lane) => <span key={lane.key}><i className={`healthcurve-key healthcurve-key--${lane.key}`} aria-hidden="true" />{lane.label} · {lane.unit}</span>)}</div>
    <div className="healthcurve-scroll" tabIndex={0} role="region" aria-label="Daily HealthCurve synchronized chart">
      <svg className="healthcurve-chart" viewBox={`0 0 ${WIDTH.toString()} ${HEIGHT.toString()}`} role="img" aria-label={`Interactive selected-day HealthCurve overlay for ${data.exposure.date} in ${data.exposure.timezone}; relative display positions share one time axis and exact values follow.`}>
        <title>Interactive selected-day HealthCurve overlay. Relative display positions are not equivalent units. Exact values follow in the readout and table.</title>
        <rect className="healthcurve-overlay-bg" x={LEFT} y={TOP} width={PLOT_WIDTH} height={PLOT_HEIGHT} />
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
          {connectedSegments(lane).map((segment, index) => <path key={`${lane.key}-${index.toString()}`} className={`healthcurve-series healthcurve-series--${lane.key}${lane.key === "exposure" ? " healthcurve-exposure-line" : ""}`} d={path(lane, segment, start, end)} />)}
          {lane.key === "exposure" ? null : lane.points.map((point, index) => <circle key={`${point.time}-${index.toString()}`} className={`healthcurve-point healthcurve-point--${lane.key}`} cx={xPosition(point.time, start, end)} cy={yPosition(lane, point.value)} r={lane.key === "symptoms" ? 6 : 3}><title>{experiencedTime(point.time, data.exposure.timezone)}: {point.label}; source {point.source}</title></circle>)}
        </g>)}
        {visible.exposure ? data.exposure.dose_markers.map((dose) => {
          const x = xPosition(dose.occurred_at, start, end);
          return <g key={dose.dose_event_id}><line className="healthcurve-dose-marker" x1={x} y1={TOP} x2={x} y2={TOP + PLOT_HEIGHT}><title>{experiencedTime(dose.occurred_at, data.exposure.timezone)}: actual dose {formatMeasurement(dose.amount, dose.unit)} {dose.medication_name}</title></line><circle className="healthcurve-dose-dot" cx={x} cy={TOP} r="5" /></g>;
        }) : null}
        <line className="healthcurve-cursor" x1={cursorX} y1={TOP} x2={cursorX} y2={TOP + PLOT_HEIGHT} />
        <rect className="healthcurve-pointer-target" x={LEFT} y={TOP} width={PLOT_WIDTH} height={PLOT_HEIGHT} onPointerMove={(event) => { const bounds = event.currentTarget.getBoundingClientRect(); moveCursor(event.clientX, bounds.left, bounds.width); }} />
        <text transform={`translate(18 ${String(TOP + PLOT_HEIGHT / 2)}) rotate(-90)`} textAnchor="middle" className="healthcurve-axis-title">Relative display position (0–100)</text>
        <text x={LEFT + PLOT_WIDTH / 2} y={HEIGHT - 8} textAnchor="middle" className="healthcurve-axis-title">Local time ({data.exposure.timezone})</text>
      </svg>
    </div>
    <label className="healthcurve-time-explorer">Explore the chart by time<input aria-label="Explore daily HealthCurve by time" type="range" min="0" max={elapsedMinutes} step="1" value={Math.min(cursorMinute, elapsedMinutes)} onChange={(event) => { setCursorMinute(Number(event.target.value)); }} /></label>
    <div className="healthcurve-readout" role="status" aria-live="polite"><strong>{experiencedTime(new Date(cursorTime).toISOString(), data.exposure.timezone)}</strong>{cursorPoints.length === 0 ? <p>No exact observation at this time. Nearby missing data remains missing.</p> : <ul>{cursorPoints.map(({ lane, point }) => <li key={`${lane.key}-${point.time}`}><strong>{lane.label}:</strong> {point.label} <span>at {experiencedTime(point.time, data.exposure.timezone)}</span></li>)}</ul>}</div>
    <div className="curve-series-summary" aria-label="Visible series sample counts">{shownLanes.map((lane) => <p key={lane.key}><strong>{lane.label}:</strong> {lane.points.length.toString()} exact point(s); {lane.unit}; gaps remain missing.</p>)}</div>
    <details className="chart-table"><summary>View exact values and provenance</summary><div className="table-scroll" tabIndex={0} role="region" aria-label="Daily HealthCurve exact values"><table><caption>Current recorded facts and deterministic model samples shown in the selected lanes. Sorting uses the experienced instant.</caption><thead><tr><th scope="col">Local date / time</th><th scope="col">Series</th><th scope="col">Exact value</th><th scope="col">Source / provenance</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.time}-${row.series}-${index.toString()}`}><th scope="row">{experiencedTime(row.time, data.exposure.timezone)}</th><td>{row.series}</td><td>{row.value}</td><td>{row.source}</td></tr>)}</tbody></table></div></details>
    <details className="metric-definition"><summary>Definitions and limitations</summary><p>{data.exposure.definition}</p><p>The overlay normalizes theoretical exposure, heart rate, HRV, respiration, and blood pressure to each series’ observed daily minimum and maximum. Garmin stress already uses 0–100. Symptoms retain their original 0–10 severity and are displayed at severity × 10 as discrete markers. Exact native values remain in the readout and table. These values are not converted into cortisol demand or a coverage ratio.</p></details>
  </section>;
}
