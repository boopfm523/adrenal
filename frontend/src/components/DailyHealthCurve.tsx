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
const LEFT = 150;
const RIGHT = 24;
const TOP = 24;
const LANE_HEIGHT = 92;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;

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

function yPosition(value: number, laneTop: number, minimum: number, maximum: number): number {
  return laneTop + 66 - (value - minimum) / Math.max(maximum - minimum, 1) * 50;
}

function path(points: Point[], start: number, end: number, laneTop: number): string {
  const bounds = scale(points.map((point) => point.value));
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${xPosition(point.time, start, end).toFixed(2)} ${yPosition(point.value, laneTop, bounds.minimum, bounds.maximum).toFixed(2)}`).join(" ");
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
  const allLanes = useMemo(() => lanes(data), [data]);
  const shownLanes = allLanes.filter((lane) => visible[lane.key]);
  const showEpisodes = visible.episodes;
  const laneCount = shownLanes.length + (showEpisodes ? 1 : 0);
  const height = TOP + laneCount * LANE_HEIGHT + 58;
  const start = Date.parse(data.exposure.day_start);
  const end = Date.parse(data.exposure.day_end);
  const ticks = timeTicks(start, end, data.exposure.timezone);
  const rows = useMemo(() => tableRows(data, visible), [data, visible]);
  let laneIndex = 0;

  return <section className="metric-card healthcurve-card" aria-labelledby="daily-healthcurve-title">
    <h2 id="daily-healthcurve-title">Your daily HealthCurve</h2>
    <p>{data.exposure.safety_label}</p>
    <aside className="association-caution"><strong>Shared time, separate meanings.</strong> The lanes align recorded events for comparison. They do not use one scale, establish causation, measure cortisol, or determine medication need.</aside>
    <fieldset className="curve-toggles"><legend>Show or hide chart lanes</legend>{Object.entries({
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
    <p className="curve-missingness"><strong>Missingness:</strong> Garmin cadence is observational, so expected missing counts are not invented. Empty intervals remain visibly blank; no interpolated values are stored.</p>
    <div className="healthcurve-scroll" tabIndex={0} role="region" aria-label="Daily HealthCurve synchronized chart">
      <svg className="healthcurve-chart" viewBox={`0 0 ${WIDTH.toString()} ${height.toString()}`} role="img" aria-label={`Selected-day HealthCurve for ${data.exposure.date} in ${data.exposure.timezone}; each lane has its own labeled scale.`}>
        <title>Selected-day HealthCurve. Each lane has its own unit and scale. Exact values follow in a table.</title>
        {ticks.map((tick, index) => {
          const x = LEFT + index / Math.max(ticks.length - 1, 1) * PLOT_WIDTH;
          return <g key={tick.time}><line className="healthcurve-time-grid" x1={x} y1={TOP} x2={x} y2={height - 38} /><text className="healthcurve-time-label" x={x} y={height - 16} textAnchor={index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle"}>{tick.label}</text></g>;
        })}
        {shownLanes.map((lane) => {
          const currentIndex = laneIndex++;
          const laneTop = TOP + currentIndex * LANE_HEIGHT;
          const bounds = scale(lane.points.map((point) => point.value));
          return <g key={lane.key} data-lane={lane.key}>
            <rect className="healthcurve-lane-bg" x={LEFT} y={laneTop} width={PLOT_WIDTH} height={LANE_HEIGHT - 8} />
            <text className="healthcurve-lane-title" x={LEFT - 12} y={laneTop + 25} textAnchor="end">{lane.label}</text>
            <text className="healthcurve-lane-unit" x={LEFT - 12} y={laneTop + 45} textAnchor="end">{lane.unit}</text>
            <text className="healthcurve-lane-range" x={LEFT - 12} y={laneTop + 64} textAnchor="end">{lane.points.length.toString()} point(s)</text>
            {lane.key === "exposure" && lane.points.length > 1 ? <path className="healthcurve-exposure-line" d={path(lane.points, start, end, laneTop)} /> : null}
            {lane.key === "exposure" && visible.exposure ? data.exposure.dose_markers.map((dose) => {
              const x = xPosition(dose.occurred_at, start, end);
              return <g key={dose.dose_event_id}><line className="healthcurve-dose-marker" x1={x} y1={laneTop + 7} x2={x} y2={laneTop + 76}><title>{experiencedTime(dose.occurred_at, data.exposure.timezone)}: actual dose {formatMeasurement(dose.amount, dose.unit)} {dose.medication_name}</title></line><circle className="healthcurve-dose-dot" cx={x} cy={laneTop + 8} r="5" /></g>;
            }) : null}
            {lane.key !== "exposure" ? lane.points.map((point, index) => <circle key={`${point.time}-${index.toString()}`} className={`healthcurve-point healthcurve-point--${lane.key}`} cx={xPosition(point.time, start, end)} cy={yPosition(point.value, laneTop, bounds.minimum, bounds.maximum)} r={lane.key === "symptoms" ? 6 : 3}><title>{experiencedTime(point.time, data.exposure.timezone)}: {point.label}; source {point.source}</title></circle>) : null}
            <text className="healthcurve-scale-label" x={LEFT + 5} y={laneTop + 15}>{formatDecimal(bounds.maximum)}</text>
            <text className="healthcurve-scale-label" x={LEFT + 5} y={laneTop + 75}>{formatDecimal(bounds.minimum)}</text>
          </g>;
        })}
        {showEpisodes ? (() => {
          const laneTop = TOP + laneIndex * LANE_HEIGHT;
          return <g data-lane="episodes"><rect className="healthcurve-lane-bg" x={LEFT} y={laneTop} width={PLOT_WIDTH} height={LANE_HEIGHT - 8} /><text className="healthcurve-lane-title" x={LEFT - 12} y={laneTop + 25} textAnchor="end">Stress episodes</text><text className="healthcurve-lane-unit" x={LEFT - 12} y={laneTop + 45} textAnchor="end">recorded intervals</text><text className="healthcurve-lane-range" x={LEFT - 12} y={laneTop + 64} textAnchor="end">{data.episodes.length.toString()} episode(s)</text>{data.episodes.map((episode) => {
            const x = Math.max(LEFT, Math.min(LEFT + PLOT_WIDTH, xPosition(episode.started_at, start, end)));
            const xEnd = Math.max(LEFT, Math.min(LEFT + PLOT_WIDTH, xPosition(episode.ended_at ?? data.exposure.day_end, start, end)));
            return <rect key={episode.id} className="healthcurve-episode" x={x} y={laneTop + 20} width={Math.max(4, xEnd - x)} height={38}><title>{experiencedTime(episode.started_at, data.exposure.timezone)}: {episode.trigger}; {episode.severity ?? "severity missing"}; {episode.status}</title></rect>;
          })}</g>;
        })() : null}
      </svg>
    </div>
    <div className="curve-series-summary" aria-label="Visible series sample counts">{shownLanes.map((lane) => <p key={lane.key}><strong>{lane.label}:</strong> {lane.points.length.toString()} exact point(s); {lane.unit}; gaps remain missing.</p>)}</div>
    <details className="chart-table"><summary>View exact values and provenance</summary><div className="table-scroll" tabIndex={0} role="region" aria-label="Daily HealthCurve exact values"><table><caption>Current recorded facts and deterministic model samples shown in the selected lanes. Sorting uses the experienced instant.</caption><thead><tr><th scope="col">Local date / time</th><th scope="col">Series</th><th scope="col">Exact value</th><th scope="col">Source / provenance</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.time}-${row.series}-${index.toString()}`}><th scope="row">{experiencedTime(row.time, data.exposure.timezone)}</th><td>{row.series}</td><td>{row.value}</td><td>{row.source}</td></tr>)}</tbody></table></div></details>
    <details className="metric-definition"><summary>Definitions and limitations</summary><p>{data.exposure.definition}</p><p>Symptoms use their original 0–10 severity. Garmin stress is a Garmin 0–100 score. HRV is milliseconds, respiration is breaths per minute, heart rate is bpm, and blood pressure is mmHg. These values are not converted into cortisol demand or a coverage ratio.</p></details>
  </section>;
}
