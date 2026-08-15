import { Button, Checkbox, Group, Paper, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useMemo, useRef, useState } from "react";

import type {
  BloodPressure,
  Episode,
  GarminRecord,
  SteroidExposureCurve,
  Symptom,
  Temperature,
} from "../api/client";
import {
  formatDecimal,
  formatMeasurement,
  garminMetricLabel,
} from "../format";
import { timezoneAbbreviation, timezoneAbbreviationForLocalDate } from "../time";

export interface DailyHealthCurveData {
  exposure: SteroidExposureCurve;
  garmin: GarminRecord[];
  symptoms: Symptom[];
  bloodPressure: BloodPressure[];
  temperature: Temperature[];
  episodes: Episode[];
}

export type HealthCurveLaneKey = "exposure" | "stress" | "heart_rate" | "hrv" | "respiration_rate" | "blood_pressure" | "temperature" | "symptoms" | "episodes";
export type HealthCurveVisibility = Record<HealthCurveLaneKey, boolean>;

type LaneKey = HealthCurveLaneKey;

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

interface SymptomObservation {
  id: string;
  time: string;
  label: string;
  source: string;
}

interface TooltipObservation {
  id: string;
  time: string;
  series: string;
  value: string;
}

interface AwakeInterval {
  id: string;
  startedAt: number;
  endedAt: number;
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
const TEMPERATURE_DISPLAY_MIN = 77;
const TEMPERATURE_DISPLAY_MAX = 113;

const DEFAULT_VISIBLE: HealthCurveVisibility = {
  exposure: true,
  stress: true,
  heart_rate: false,
  hrv: false,
  respiration_rate: false,
  blood_pressure: false,
  temperature: false,
  symptoms: false,
  episodes: false,
};

const FOCUS_PRESETS: readonly { label: string; keys: readonly LaneKey[] }[] = [
  { label: "Stress", keys: ["exposure", "stress"] },
  { label: "Heart rate", keys: ["exposure", "heart_rate"] },
  { label: "HRV", keys: ["exposure", "hrv"] },
  { label: "Respiration", keys: ["exposure", "respiration_rate"] },
  { label: "Blood pressure", keys: ["exposure", "blood_pressure"] },
  { label: "Temperature", keys: ["exposure", "temperature"] },
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

function isPhysiologicalCurve(exposure: SteroidExposureCurve): exposure is Extract<SteroidExposureCurve, { series_unit: "nmol/L" }> {
  return exposure.series_unit === "nmol/L";
}

function exposureModelVersion(exposure: SteroidExposureCurve): string {
  return isPhysiologicalCurve(exposure) ? exposure.model.revision : exposure.model.version;
}

function exposureValue(sample: SteroidExposureCurve["samples"][number]): string {
  return "modeled_free_cortisol_nmol_l" in sample
    ? sample.modeled_free_cortisol_nmol_l
    : sample.theoretical_exposure_reu;
}

function regularExposureValue(sample: SteroidExposureCurve["samples"][number]): string {
  return "regular_modeled_free_cortisol_nmol_l" in sample
    ? sample.regular_modeled_free_cortisol_nmol_l
    : sample.regular_exposure_reu;
}

function stressExposureValue(sample: SteroidExposureCurve["samples"][number]): string {
  return "stress_modeled_free_cortisol_nmol_l" in sample
    ? sample.stress_modeled_free_cortisol_nmol_l
    : sample.stress_exposure_reu;
}

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

function localClockTime(value: number, timezone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

function localDate(value: number, timezone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

function awakeIntervalValue(interval: AwakeInterval, timezone: string): string {
  const totalSeconds = Math.max(1, Math.round((interval.endedAt - interval.startedAt) / 1_000));
  const hours = Math.floor(totalSeconds / 3_600);
  const minutes = Math.floor(totalSeconds % 3_600 / 60);
  const seconds = totalSeconds % 60;
  const durationParts = [
    hours > 0 ? `${hours.toString()} ${hours === 1 ? "hour" : "hours"}` : null,
    minutes > 0 ? `${minutes.toString()} ${minutes === 1 ? "minute" : "minutes"}` : null,
    seconds > 0 ? `${seconds.toString()} ${seconds === 1 ? "second" : "seconds"}` : null,
  ].filter((part): part is string => part !== null);
  const startedDate = localDate(interval.startedAt, timezone);
  const endedDate = localDate(interval.endedAt, timezone);
  const startedAt = localClockTime(interval.startedAt, timezone);
  const endedAt = localClockTime(interval.endedAt, timezone);
  const localRange = startedDate === endedDate
    ? `${startedAt}–${endedAt} local`
    : `${startedDate} ${startedAt}–${endedDate} ${endedAt} local`;
  return `${durationParts.join(" ")} · ${localRange}`;
}

interface TimeTick {
  time: number;
  label: string | null;
}

function timeTicks(start: number, end: number, timezone: string): TimeTick[] {
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const ticks: TimeTick[] = [];
  for (let time = start; time <= end; time += 60 * 60 * 1_000) {
    const label = formatter.format(new Date(time));
    const hour = Number(label.slice(0, 2));
    ticks.push({ time, label: hour % 4 === 0 ? label : null });
  }
  if (ticks.at(-1)?.time !== end) {
    const label = formatter.format(new Date(end));
    ticks.push({ time: end, label });
  }
  return ticks;
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
        : lane.key === "temperature" ? { minimum: TEMPERATURE_DISPLAY_MIN, maximum: TEMPERATURE_DISPLAY_MAX }
      : scale(lane.points.map((point) => point.value));
  const relative = (value - bounds.minimum) / Math.max(bounds.maximum - bounds.minimum, 1) * 100;
  return ["respiration_rate", "temperature"].includes(lane.key) ? Math.max(0, Math.min(100, relative)) : relative;
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

function dailyAggregatesForLane(data: DailyHealthCurveData, lane: Lane): GarminRecord[] {
  if (lane.key === "heart_rate") {
    return data.garmin.filter(
      (record) => record.kind === "daily" && ["heart_rate", "resting_heart_rate"].includes(record.metric_type ?? ""),
    );
  }
  const definition = METRIC_LANES.find((candidate) => candidate.key === lane.key);
  if (definition === undefined) return [];
  return data.garmin.filter(
    (record) => record.kind === "daily" && record.metric_type === definition.metric,
  );
}

function dailyAggregateValue(record: GarminRecord): string {
  if (record.metric_type === "stress") return formatDecimal(record.value);
  return formatMeasurement(record.value, record.unit);
}

function summaryNumber(value: number, maximumFractionDigits = 1): string {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(value);
}

function summaryValues(lane: Lane, data: DailyHealthCurveData): number[] {
  if (lane.key === "exposure") return lane.points.map((point) => point.value);
  const definition = METRIC_LANES.find((candidate) => candidate.key === lane.key);
  if (definition === undefined) return [];
  return data.garmin.flatMap((record) => {
    if (record.kind !== "sample" || record.metric_type !== definition.metric) return [];
    const value = numeric(record.value);
    return value === null ? [] : [value];
  });
}

function laneMetadata(lane: Lane, data: DailyHealthCurveData, unscoredSymptomCount: number): string {
  if (lane.key === "blood_pressure") return `${data.bloodPressure.length.toString()} recorded measurement(s); values use mmHg; missing times remain missing.`;
  if (lane.key === "temperature") return `${data.temperature.length.toString()} recorded measurement(s); the graph uses a fixed ${TEMPERATURE_DISPLAY_MIN.toString()}–${TEMPERATURE_DISPLAY_MAX.toString()} °F display range and exact Fahrenheit and Celsius values remain in the Timeline.`;
  if (lane.key === "symptoms") return `${data.symptoms.length.toString()} recorded event(s); ${lane.points.length.toString()} with severity and ${unscoredSymptomCount.toString()} without severity. Missing severity is not treated as zero.`;
  const displayNote = lane.key === "respiration_rate"
    ? ` The graph uses a display-only 5-sample rolling median within each contiguous segment and a fixed 0–${RESPIRATION_DISPLAY_MAX.toString()} breaths/min range; underlying values are unchanged.`
    : ["stress", "heart_rate", "hrv"].includes(lane.key)
      ? " Dense sample dots are hidden from the graph."
      : "";
  return `${summaryValues(lane, data).length.toString()} exact point(s); ${lane.unit}; gaps remain missing.${displayNote} Exact values remain in the chart tooltip and Timeline.`;
}

function SummaryInfo({ id, label, children }: { id: string; label: string; children: string }): React.JSX.Element {
  const [focused, setFocused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const visible = focused || hovered;
  return <span className="curve-summary-info">
    <button type="button" className="curve-summary-info-button" aria-label={`About ${label} data`} aria-describedby={visible ? id : undefined} onFocus={() => { setFocused(true); }} onBlur={() => { setFocused(false); }} onPointerEnter={() => { setHovered(true); }} onPointerLeave={() => { setHovered(false); }}>ⓘ</button>
    {visible ? <span id={id} className="curve-summary-tooltip" role="tooltip">{children}</span> : null}
  </span>;
}

function lanes(data: DailyHealthCurveData): Lane[] {
  const exposure: Lane = {
    key: "exposure",
    label: isPhysiologicalCurve(data.exposure) ? data.exposure.series_name : "Theoretical exposure",
    unit: data.exposure.series_unit,
    points: data.exposure.samples.map((sample) => ({
      time: sample.occurred_at,
      value: Number(exposureValue(sample)),
      label: formatMeasurement(exposureValue(sample), data.exposure.series_unit),
      source: exposureModelVersion(data.exposure),
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
  const temperature: Lane = {
    key: "temperature",
    label: "Temperature",
    unit: "°F (°C)",
    points: data.temperature.map((record) => ({
      time: record.time.occurred_at,
      value: Number(record.display_f),
      label: `${record.display_f} °F (${record.display_c} °C)`,
      source: `${record.provenance.source_type}; ${record.provenance.confirmation_state}`,
    })),
  };
  return [exposure, ...metricSeries, bloodPressure, temperature, symptoms];
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

function sleepTooltipObservations(
  records: GarminRecord[],
  dayStart: number,
  dayEnd: number,
): TooltipObservation[] {
  return records.flatMap((record) => {
    const observations: TooltipObservation[] = [];
    const sessionStart = Date.parse(record.time.occurred_at);
    const sessionEnd = Date.parse(record.ended_at ?? record.time.occurred_at);
    const awakeIntervals = record.sleep_intervals ?? [];
    const startIsVisible = sessionStart >= dayStart && sessionStart < dayEnd;
    const endIsVisible = sessionEnd > dayStart && sessionEnd <= dayEnd;

    if (startIsVisible) {
      observations.push({
        id: `${record.id}-sleep-start`,
        time: record.time.occurred_at,
        series: "Sleep",
        value: "started",
      });
    }
    if (endIsVisible) {
      observations.push({
        id: `${record.id}-sleep-end`,
        time: record.ended_at ?? record.time.occurred_at,
        series: "Sleep",
        value: "final wake / sleep ended",
      });
    }
    for (const [index, interval] of awakeIntervals.entries()) {
      const intervalStart = Date.parse(interval.started_at);
      const intervalEnd = Date.parse(interval.ended_at);
      if (intervalStart >= dayStart && intervalStart < dayEnd) {
        observations.push({
          id: `${record.id}-awake-${index.toString()}-start`,
          time: interval.started_at,
          series: "Awakening",
          value: "started",
        });
      }
      if (intervalEnd > dayStart && intervalEnd <= dayEnd) {
        observations.push({
          id: `${record.id}-awake-${index.toString()}-end`,
          time: interval.ended_at,
          series: "Awakening",
          value: "ended",
        });
      }
    }

    const awakeningCount = record.awakenings ?? 0;
    if (awakeningCount > 0 && awakeIntervals.length === 0) {
      const boundaryTime = startIsVisible
        ? record.time.occurred_at
        : endIsVisible ? (record.ended_at ?? record.time.occurred_at) : null;
      if (boundaryTime !== null) {
        observations.push({
          id: `${record.id}-untimed-awakenings`,
          time: boundaryTime,
          series: "Awakenings",
          value: `${awakeningCount.toString()} reported; exact ${awakeningCount === 1 ? "time" : "times"} unavailable`,
        });
      }
    }
    return observations;
  });
}

function nearbyTooltipObservations(
  observations: TooltipObservation[],
  cursorTime: number,
): TooltipObservation[] {
  return observations.filter(
    (observation) => Math.abs(Date.parse(observation.time) - cursorTime) <= 60_000,
  );
}

function explicitAwakeIntervals(records: GarminRecord[]): AwakeInterval[] {
  const intervals = records.flatMap((record) => (record.sleep_intervals ?? []).flatMap((interval, index) => {
    const startedAt = Date.parse(interval.started_at);
    const endedAt = Date.parse(interval.ended_at);
    if (!Number.isFinite(startedAt) || !Number.isFinite(endedAt) || endedAt <= startedAt) return [];
    return [{
      id: `${record.id}-awake-${index.toString()}`,
      startedAt,
      endedAt,
    }];
  })).sort((left, right) => left.startedAt - right.startedAt || left.endedAt - right.endedAt);

  return intervals.reduce<AwakeInterval[]>((merged, interval) => {
    const previous = merged.at(-1);
    if (previous === undefined || interval.startedAt > previous.endedAt) {
      merged.push(interval);
      return merged;
    }
    previous.endedAt = Math.max(previous.endedAt, interval.endedAt);
    return merged;
  }, []);
}

function doseTooltipObservations(
  exposure: SteroidExposureCurve,
  dayStart: number,
  dayEnd: number,
): TooltipObservation[] {
  return [...exposure.dose_markers]
    .sort((left, right) => {
      const timeDifference = Date.parse(left.occurred_at) - Date.parse(right.occurred_at);
      return timeDifference === 0
        ? left.dose_event_id.localeCompare(right.dose_event_id)
        : timeDifference;
    })
    .flatMap((dose) => {
      const occurredAt = Date.parse(dose.occurred_at);
      if (occurredAt < dayStart || occurredAt >= dayEnd) return [];
      return [{
        id: `dose-${dose.dose_event_id}`,
        time: dose.occurred_at,
        series: dose.category === "stress" ? "Stress dose" : "Regular dose",
        value: `${dose.medication_name} ${formatMeasurement(dose.amount, dose.unit)}`,
      }];
    });
}

interface DailyHealthCurveProps {
  data: DailyHealthCurveData;
  visible?: HealthCurveVisibility;
  onVisibleChange?: (visible: HealthCurveVisibility) => void;
  onPreviousDay?: () => void;
  onNextDay?: () => void;
  nextDayDisabled?: boolean;
}

export function DailyHealthCurve({
  data,
  visible: controlledVisible,
  onVisibleChange,
  onPreviousDay,
  onNextDay,
  nextDayDisabled = false,
}: DailyHealthCurveProps): React.JSX.Element {
  const [localVisible, setLocalVisible] = useState(DEFAULT_VISIBLE);
  const visible = controlledVisible ?? localVisible;

  function setVisible(next: HealthCurveVisibility): void {
    if (controlledVisible === undefined) setLocalVisible(next);
    onVisibleChange?.(next);
  }

  const start = Date.parse(data.exposure.day_start);
  const end = Date.parse(data.exposure.day_end);
  const [cursorMinute, setCursorMinute] = useState(0);
  const [hoveringChart, setHoveringChart] = useState(false);
  const [touchSelected, setTouchSelected] = useState(false);
  const [chartZoom, setChartZoom] = useState<1 | 1.5 | 2>(1);
  const activeTouchPointer = useRef<number | null>(null);
  const cursorTime = Math.min(end, start + cursorMinute * 60_000);
  const allLanes = useMemo(() => lanes(data), [data]);
  const shownLanes = allLanes.filter((lane) => visible[lane.key]);
  const ticks = timeTicks(start, end, data.exposure.timezone);
  const sleepRecords = data.garmin.filter((record) => record.kind === "sleep" && record.ended_at != null);
  const missingWakeTiming = sleepRecords.some(
    (record) => (record.awakenings ?? 0) > 0 && (record.sleep_intervals ?? []).length === 0,
  );
  const sleepObservations = sleepTooltipObservations(sleepRecords, start, end);
  const cursorSleepObservations = nearbyTooltipObservations(sleepObservations, cursorTime);
  const awakeIntervals = explicitAwakeIntervals(sleepRecords);
  const cursorAwakeIntervals = awakeIntervals.filter(
    (interval) => cursorTime >= interval.startedAt && cursorTime < interval.endedAt,
  );
  const doseObservations = doseTooltipObservations(data.exposure, start, end);
  const cursorDoseObservations = visible.exposure
    ? nearbyTooltipObservations(doseObservations, cursorTime)
    : [];
  const cursorPoints = nearestVisiblePoints(shownLanes, cursorTime);
  const cursorExposurePoint = cursorPoints.find(({ lane }) => lane.key === "exposure")?.point;
  const cursorExposureSample = cursorExposurePoint === undefined
    ? undefined
    : data.exposure.samples.find((sample) => sample.occurred_at === cursorExposurePoint.time);
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
        value: lane.key === "exposure" ? `${point.value.toFixed(3)} ${data.exposure.series_unit}` : point.label,
      }];
    }),
    ...(cursorExposureSample === undefined ? [] : [
      ...(Number(regularExposureValue(cursorExposureSample)) > 0 ? [{
        key: `regular-exposure-${cursorExposureSample.occurred_at}`,
        series: "Regular-dose contribution",
        value: `${Number(regularExposureValue(cursorExposureSample)).toFixed(3)} ${data.exposure.series_unit}`,
      }] : []),
      ...(Number(stressExposureValue(cursorExposureSample)) > 0 ? [{
        key: `stress-exposure-${cursorExposureSample.occurred_at}`,
        series: "Stress-dose contribution",
        value: `${Number(stressExposureValue(cursorExposureSample)).toFixed(3)} ${data.exposure.series_unit}`,
      }] : []),
    ]),
    ...cursorUnscoredSymptoms.map((symptom) => ({
      key: `unscored-symptom-${symptom.id}`,
      series: "Symptoms",
      value: symptom.label,
    })),
    ...cursorSleepObservations.map((observation) => ({
      key: observation.id,
      series: observation.series,
      value: observation.value,
    })),
    ...cursorAwakeIntervals.map((interval) => ({
      key: interval.id,
      series: "Awake interval",
      value: awakeIntervalValue(interval, data.exposure.timezone),
    })),
    ...cursorDoseObservations.map((observation) => ({
      key: observation.id,
      series: observation.series,
      value: observation.value,
    })),
  ];
  const cursorLabel = experiencedTime(new Date(cursorTime).toISOString(), data.exposure.timezone);
  const cursorX = LEFT + (cursorTime - start) / Math.max(end - start, 1) * PLOT_WIDTH;
  const tooltipWidth = 280;
  const tooltipX = Math.min(WIDTH - tooltipWidth - 8, Math.max(LEFT + 8, cursorX + (cursorX > WIDTH * 0.62 ? -tooltipWidth - 12 : 12)));
  const tooltipHeight = Math.min(PLOT_HEIGHT - 24, 52 + Math.max(1, cursorRows.length) * 26);
  const navigableTimes: number[] = [...new Set<number>([
    ...shownLanes.flatMap((lane) => lane.points.map((point) => Date.parse(point.time))),
    ...(visible.exposure ? doseObservations.map((observation) => Date.parse(observation.time)) : []),
    ...sleepObservations.map((observation) => Date.parse(observation.time)),
    ...(visible.symptoms ? unscoredSymptoms.map((observation) => Date.parse(observation.time)) : []),
    ...awakeIntervals.flatMap((interval) => [interval.startedAt, interval.endedAt]),
  ])].filter((time) => Number.isFinite(time) && time >= start && time <= end).sort((left, right) => left - right);
  const previousObservation = [...navigableTimes].reverse().find((time) => time < cursorTime);
  const nextObservation = navigableTimes.find((time) => time > cursorTime);

  function moveCursor(clientX: number, left: number, width: number): void {
    const ratio = Math.max(0, Math.min(1, (clientX - left) / Math.max(width, 1)));
    setCursorMinute(Math.round(ratio * (end - start) / 60_000));
  }

  function selectTime(time: number): void {
    setCursorMinute(Math.round((Math.max(start, Math.min(end, time)) - start) / 60_000));
    setTouchSelected(true);
  }

  return <Paper component="section" className="healthcurve-card" withBorder radius="lg" p={{ base: "md", sm: "lg" }} aria-labelledby="daily-healthcurve-title">
    <Title order={2} id="daily-healthcurve-title">Your daily HealthCurve</Title>
    <Text mt="xs">{data.exposure.safety_label}</Text>
    <dl className="metric-metadata"><div><dt>Selected date</dt><dd className="healthcurve-selected-day">{onPreviousDay === undefined ? null : <button type="button" className="button-secondary" aria-label="Review previous day" onClick={onPreviousDay}>←</button>}<span>{data.exposure.date}</span>{onNextDay === undefined ? null : <button type="button" className="button-secondary" aria-label="Review next day" disabled={nextDayDisabled} onClick={onNextDay}>→</button>}</dd></div><div><dt>Timezone</dt><dd>{timezoneAbbreviationForLocalDate(data.exposure.timezone, data.exposure.date)}</dd></div><div><dt>Elapsed day</dt><dd>{formatDecimal(data.exposure.elapsed_hours)} hours</dd></div></dl>
    <details className="metric-definition healthcurve-context">
      <summary>HealthCurve context and limits</summary>
      <div className="healthcurve-context-content">
        <dl className="metric-metadata healthcurve-context-model"><div><dt>Exposure model</dt><dd>{exposureModelVersion(data.exposure)}</dd></div></dl>
        <aside className="association-caution"><strong>Association does not establish causation.</strong> These summaries describe the selected records. They do not determine why a symptom, dose, or episode occurred and are not medical advice.</aside>
        <aside className="association-caution"><strong>Focused comparison on one time axis.</strong> The graph starts with theoretical exposure and Garmin stress so the shape stays readable. Choose another focus below or opt into the deliberately busy all-series view. Every enabled series uses a relative 0–100 display scale. Exact values keep their original units in the hover tooltip and authoritative Timeline. Relative heights are not equivalent measurements, do not establish causation, do not measure cortisol, and do not determine medication need.</aside>
        <p className="curve-missingness"><strong>Missingness:</strong> Garmin cadence is observational, so expected missing counts are not invented. Lines connect only contiguous samples with an observed cadence. Unknown or interrupted intervals remain blank; no interpolated values are stored as facts.{missingWakeTiming ? " Garmin reported one or more awakenings without their exact times, so no intermediate wake markers are invented for those sessions." : ""}</p>
      </div>
    </details>
    <Stack className="healthcurve-controls" gap="sm" aria-label="HealthCurve chart controls"><Group className="healthcurve-focus" gap="xs" role="group" aria-label="Choose a focused HealthCurve comparison"><Text fw={750}>Quick focus:</Text>{FOCUS_PRESETS.map((preset) => <Button key={preset.label} type="button" size="sm" variant={isPresetVisible(visible, preset.keys) ? "filled" : "outline"} aria-pressed={isPresetVisible(visible, preset.keys)} onClick={() => { setVisible(presetVisibility(preset.keys)); }}>{preset.label}</Button>)}</Group>
      <Paper component="fieldset" className="curve-toggles" withBorder radius="md" p="md"><legend>Show or hide chart series</legend><SimpleGrid cols={{ base: 1, xs: 2, md: 3 }}>{Object.entries({
        exposure: isPhysiologicalCurve(data.exposure) ? "Physiological scenario and actual doses" : "Theoretical exposure and actual doses",
        stress: "Garmin stress",
        heart_rate: "Heart rate",
        hrv: "HRV",
        respiration_rate: "Respiration",
        blood_pressure: "Blood pressure",
        temperature: "Temperature",
        symptoms: "Symptoms",
        episodes: "Stress episodes",
      } satisfies Record<LaneKey, string>).map(([key, label]) => <Checkbox key={key} label={label} checked={visible[key as LaneKey]} onChange={(event) => { setVisible({ ...visible, [key]: event.target.checked }); }} />)}</SimpleGrid></Paper></Stack>
    <div className="healthcurve-legend" aria-label="Overlay series legend">{sleepRecords.length === 0 ? null : <><span><i className="healthcurve-key healthcurve-key--sleep" aria-hidden="true" />Sleep session</span><span><i className="healthcurve-key healthcurve-key--awake" aria-hidden="true" />Explicit awake interval</span></>}{shownLanes.map((lane) => <span key={lane.key}><i className={`healthcurve-key healthcurve-key--${lane.key}`} aria-hidden="true" />{lane.label} · {lane.unit}{lane.key === "respiration_rate" ? " · calmer 5-sample median line" : ""}</span>)}</div>
    <div className="healthcurve-mobile-controls" role="group" aria-label="Mobile chart controls">
      <span>Chart zoom</span>
      <button type="button" className="button-secondary" aria-label="Zoom chart out" disabled={chartZoom === 1} onClick={() => { setChartZoom(chartZoom === 2 ? 1.5 : 1); }}>−</button>
      <output aria-live="polite">{chartZoom.toString()}×</output>
      <button type="button" className="button-secondary" aria-label="Zoom chart in" disabled={chartZoom === 2} onClick={() => { setChartZoom(chartZoom === 1 ? 1.5 : 2); }}>+</button>
    </div>
    <div className="healthcurve-scroll" tabIndex={0} role="region" aria-label="Daily HealthCurve synchronized chart">
      <svg className={`healthcurve-chart healthcurve-chart--zoom-${chartZoom.toString().replace(".", "-")}`} viewBox={`0 0 ${WIDTH.toString()} ${HEIGHT.toString()}`} role="img" aria-label={`Interactive selected-day HealthCurve overlay for ${data.exposure.date} in ${timezoneAbbreviationForLocalDate(data.exposure.timezone, data.exposure.date)}; ${data.symptoms.length.toString()} recorded symptom ${data.symptoms.length === 1 ? "event" : "events"}; relative display positions share one time axis and exact values follow.`}>
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
            {sessionStart >= start && sessionStart < end ? <><line className="healthcurve-sleep-marker healthcurve-sleep-marker--start" x1={startX} y1={TOP} x2={startX} y2={TOP + PLOT_HEIGHT} /><text aria-hidden="true" className="healthcurve-sleep-label" x={startX + 4} y={TOP + 27}>Sleep start</text></> : null}
            {sessionEnd > start && sessionEnd <= end ? <><line className="healthcurve-sleep-marker healthcurve-sleep-marker--end" x1={endX} y1={TOP} x2={endX} y2={TOP + PLOT_HEIGHT} /><text aria-hidden="true" className="healthcurve-sleep-label" x={endX + 4} y={TOP + 27}>Wake</text></> : null}
          </g>;
        })}{awakeIntervals.map((interval) => {
          const awakeStart = Math.max(start, interval.startedAt);
          const awakeEnd = Math.min(end, interval.endedAt);
          if (awakeEnd <= awakeStart) return null;
          const awakeX = LEFT + (awakeStart - start) / Math.max(end - start, 1) * PLOT_WIDTH;
          const awakeEndX = LEFT + (awakeEnd - start) / Math.max(end - start, 1) * PLOT_WIDTH;
          return <rect key={interval.id} className="healthcurve-awake-interval" x={awakeX} y={TOP} width={Math.max(2, awakeEndX - awakeX)} height={PLOT_HEIGHT}><title>{awakeIntervalValue(interval, data.exposure.timezone)}: explicit Garmin awake interval</title></rect>;
        })}</g>}
        {[0, 25, 50, 75, 100].map((relative) => {
          const y = TOP + PLOT_HEIGHT - relative / 100 * PLOT_HEIGHT;
          return <g key={relative}><line className="healthcurve-relative-grid" x1={LEFT} y1={y} x2={LEFT + PLOT_WIDTH} y2={y} /><text className="healthcurve-scale-label" x={LEFT - 10} y={y} dy="0.35em" textAnchor="end">{relative.toString()}</text></g>;
        })}
        {ticks.map((tick) => {
          const x = LEFT + (tick.time - start) / Math.max(end - start, 1) * PLOT_WIDTH;
          return <g key={tick.time} className={tick.label === null ? "healthcurve-hour-tick" : "healthcurve-major-time-tick"}>
            {tick.label === null ? null : <line className="healthcurve-time-grid" x1={x} y1={TOP} x2={x} y2={TOP + PLOT_HEIGHT} />}
            <line className="healthcurve-hour-mark" x1={x} y1={TOP + PLOT_HEIGHT} x2={x} y2={TOP + PLOT_HEIGHT + (tick.label === null ? 5 : 8)} />
            {tick.label === null ? null : <text className="healthcurve-time-label" x={x} y={HEIGHT - 34} textAnchor={tick.time === start ? "start" : tick.time === end ? "end" : "middle"}>{tick.label}</text>}
          </g>;
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
          {lane.key === "temperature" ? lane.points.map((point, index) => <circle key={`${point.time}-${index.toString()}`} className={`healthcurve-point healthcurve-point--${lane.key}`} cx={xPosition(point.time, start, end)} cy={yPosition(lane, point.value)} r="6"><title>{experiencedTime(point.time, data.exposure.timezone)}: Temperature {point.label}</title></circle>) : null}
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
        <rect className="healthcurve-pointer-target" x={LEFT} y={TOP} width={PLOT_WIDTH} height={PLOT_HEIGHT}
          onPointerEnter={(event) => { if (event.pointerType !== "touch") setHoveringChart(true); }}
          onPointerLeave={(event) => { if (event.pointerType !== "touch") setHoveringChart(false); }}
          onPointerDown={(event) => { if (event.pointerType !== "touch") return; const bounds = event.currentTarget.getBoundingClientRect(); activeTouchPointer.current = event.pointerId; event.currentTarget.setPointerCapture(event.pointerId); moveCursor(event.clientX, bounds.left, bounds.width); setTouchSelected(true); setHoveringChart(false); }}
          onPointerUp={(event) => { if (activeTouchPointer.current !== event.pointerId) return; activeTouchPointer.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
          onPointerCancel={() => { activeTouchPointer.current = null; }}
          onPointerMove={(event) => { if (event.pointerType === "touch" && activeTouchPointer.current !== event.pointerId) return; const bounds = event.currentTarget.getBoundingClientRect(); moveCursor(event.clientX, bounds.left, bounds.width); }} />
        {hoveringChart || touchSelected ? <foreignObject className={`healthcurve-hover-tooltip${touchSelected && !hoveringChart ? " healthcurve-touch-tooltip" : ""}`} x={tooltipX} y={TOP + 12} width={tooltipWidth} height={tooltipHeight}><div className="healthcurve-hover-tooltip-card" role="tooltip"><strong>{cursorLabel}</strong>{cursorRows.length === 0 ? <p>No exact observation at this time.</p> : <ul>{cursorRows.map((row) => <li key={`tooltip-${row.key}`}><strong>{row.series}:</strong> {row.value}</li>)}</ul>}</div></foreignObject> : null}
        <text transform={`translate(18 ${String(TOP + PLOT_HEIGHT / 2)}) rotate(-90)`} textAnchor="middle" className="healthcurve-axis-title">Relative display position (0–100)</text>
        <text x={LEFT + PLOT_WIDTH / 2} y={HEIGHT - 8} textAnchor="middle" className="healthcurve-axis-title">Local time ({timezoneAbbreviation(data.exposure.timezone, data.exposure.day_start)})</text>
      </svg>
    </div>
    <section className="healthcurve-touch-readout" aria-label="Selected chart time and values">
      {!touchSelected ? <p><strong>Explore the graph:</strong> tap a time, then drag left or right. Vertical swipes continue to scroll the page.</p> : <><h3>{cursorLabel}</h3>{cursorRows.length === 0 ? <p>No exact observation at this time.</p> : <ul>{cursorRows.map((row) => <li key={`touch-${row.key}`}><strong>{row.series}:</strong> {row.value}</li>)}</ul>}</>}
      <div className="healthcurve-touch-navigation">
        <button type="button" className="button-secondary" disabled={previousObservation === undefined} onClick={() => { if (previousObservation !== undefined) selectTime(previousObservation); }}>← Previous observation</button>
        <button type="button" className="button-secondary" disabled={nextObservation === undefined} onClick={() => { if (nextObservation !== undefined) selectTime(nextObservation); }}>Next observation →</button>
      </div>
    </section>
    {data.symptoms.length > 0 ? <section className="healthcurve-recorded-symptoms" aria-labelledby="healthcurve-recorded-symptoms-title"><h3 id="healthcurve-recorded-symptoms-title">Recorded symptoms</h3><p>Symptoms without a recorded severity use a time marker below the numeric scale; HealthCurve does not treat missing severity as zero.</p><ul>{data.symptoms.map((symptom) => <li key={symptom.id}><time dateTime={symptom.time.occurred_at}>{experiencedTime(symptom.time.occurred_at, data.exposure.timezone)}</time>: <strong>{symptom.name}</strong> — {symptom.severity == null ? "severity not recorded" : `${symptom.severity.toString()}/10`}</li>)}</ul></section> : null}
    <div className="curve-series-summary" aria-label="Series sample counts">{allLanes.map((lane) => {
      const aggregates = dailyAggregatesForLane(data, lane);
      const values = summaryValues(lane, data);
      const average = values.length === 0 ? null : values.reduce((total, value) => total + value, 0) / values.length;
      const metadataId = `curve-summary-metadata-${lane.key}`;
      return <section key={lane.key} className="curve-summary-card" aria-labelledby={`curve-summary-title-${lane.key}`}>
        <h3 id={`curve-summary-title-${lane.key}`}>{lane.label}<SummaryInfo id={metadataId} label={lane.label}>{laneMetadata(lane, data, unscoredSymptoms.length)}</SummaryInfo></h3>
        <div className="curve-summary-values">
          {aggregates.map((record) => <p key={record.id}><strong>{record.measurement_label ?? garminMetricLabel(record.metric_type)}:</strong> {dailyAggregateValue(record)}</p>)}
          {lane.key === "exposure" ? <p><strong>Peak:</strong> {summaryNumber(Math.max(...values, 0), 3)} {data.exposure.series_unit}</p> : null}
          {["stress", "heart_rate", "hrv", "respiration_rate"].includes(lane.key) && average !== null ? <><p><strong>Observed average:</strong> {summaryNumber(average)} {lane.unit}</p><p><strong>Observed range:</strong> {summaryNumber(Math.min(...values))}–{summaryNumber(Math.max(...values))} {lane.unit}</p></> : null}
          {lane.key === "blood_pressure" ? data.bloodPressure.map((record) => <p key={record.id}><strong>{record.systolic_mmhg.toString()}/{record.diastolic_mmhg.toString()} mmHg</strong>{record.pulse_bpm == null ? null : ` · pulse ${record.pulse_bpm.toString()} bpm`}</p>) : null}
          {lane.key === "temperature" ? data.temperature.map((record) => <p key={record.id}><strong>{record.display_f} °F</strong> ({record.display_c} °C)</p>) : null}
          {lane.key === "symptoms" ? data.symptoms.map((record) => <p key={record.id}><strong>{record.name}</strong>{record.severity == null ? " · severity not recorded" : ` · ${record.severity.toString()}/10`}</p>) : null}
          {aggregates.length === 0 && values.length === 0 && lane.key !== "symptoms" ? <p className="muted">No values recorded.</p> : null}
          {lane.key === "symptoms" && data.symptoms.length === 0 ? <p className="muted">No symptoms recorded.</p> : null}
        </div>
      </section>;
    })}</div>
    {data.temperature.length === 0 ? null : <div className="table-scroll" tabIndex={0} role="region" aria-label="Selected-day temperature exact values"><table className="vital-table"><caption>Exact selected-day body-temperature facts. Missing intervals remain blank.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Temperature</th></tr></thead><tbody>{data.temperature.map((record) => <tr key={record.id}><td>{experiencedTime(record.time.occurred_at, data.exposure.timezone)}</td><td>{record.display_f} °F ({record.display_c} °C)</td></tr>)}</tbody></table></div>}
    <details className="metric-definition model-methodology"><summary>How this model works: formulas, sources, and limits</summary>
      <p>{data.exposure.definition}</p>
      <h3>Exact implemented exposure formula</h3>
      <p><strong>{exposureModelVersion(data.exposure)}</strong> supports only {data.exposure.model.supported_formulation} {data.exposure.model.supported_route} {data.exposure.model.supported_medication} recorded in {data.exposure.model.amount_unit}.</p>
      {isPhysiologicalCurve(data.exposure) ? <>
        <pre><code>{`ka = ${formatDecimal(data.exposure.model.absorption_rate_per_hour)} per hour\nke = clearance / distribution volume = ${formatDecimal(data.exposure.model.elimination_rate_per_hour)} per hour\nt_peak = ln(ka / ke) / (ka - ke) = ${formatDecimal(data.exposure.model.peak_time_hours)} hours\nC(t) = (F × dose / V) × (1,000,000 / molecular weight) × ka/(ka-ke) × (exp(-ke×t) - exp(-ka×t)) nmol/L`}</code></pre>
        <p>This is a population-parameter plasma-free-cortisol scenario. It is not a measured value, personal target, medication-adequacy test, or dosing guide. Contributions from close or simultaneous doses are summed and sampled every {data.exposure.model.sample_interval_minutes.toString()} elapsed minutes plus exact administration and modeled-peak knots.</p>
      </> : <>
        <pre><code>{`ka = ${formatDecimal(data.exposure.model.absorption_rate_per_hour)} per hour\nke = ln(2) / ${formatDecimal(data.exposure.model.elimination_half_life_hours)} hours = ${formatDecimal(data.exposure.model.elimination_rate_per_hour)} per hour\nt_peak = ln(ka / ke) / (ka - ke) = ${formatDecimal(data.exposure.model.peak_time_hours)} hours\nraw(t) = exp(-ke × t) - exp(-ka × t)\nshape(t) = raw(t) / raw(t_peak)\ndose_contribution(t) = recorded_amount_mg × shape(t) REU\nstress_exposure(t) = sum of explicitly categorized stress-dose contributions\nregular_exposure(t) = sum of all other supported dose contributions\ntotal_exposure(t) = regular_exposure(t) + stress_exposure(t)`}</code></pre>
        <p>Each contribution is zero before its recorded administration and after {data.exposure.model.contribution_horizon_hours.toString()} hours. It rises from zero, reaches a normalized peak of 1 REU per recorded mg at <code>t_peak</code>, then declines. Contributions from close or simultaneous doses are summed; none resets another. Output is sampled every {data.exposure.model.sample_interval_minutes.toString()} elapsed minutes plus exact administration and modeled-peak knots. REU is a relative visualization unit, not nmol/L, µg/dL, biological effect, or medication adequacy.</p>
      </>}
      <h3>Why these parameters are used</h3>
      <ul>{data.exposure.model.references.map((href) => { const detail = EXPOSURE_REFERENCE_DETAILS[href]; return <li key={href}><a href={href} target="_blank" rel="noreferrer">{detail?.label ?? "Model source"}</a>{detail === undefined ? null : ` — ${detail.use}.`}</li>; })}</ul>
      <h3>No “needed cortisol” formula is active</h3>
      <p>{isPhysiologicalCurve(data.exposure) ? "HealthCurve calculates no Garmin-stress-derived or symptom-derived cortisol “needed” value." : "HealthCurve currently calculates no baseline, Garmin-stress-derived, or symptom-derived cortisol “needed” value. The supplied exploratory scenario used Req(t) = Base(t) × S(t), but its population baseline anchors and stress multipliers are not part of hc-exposure-v1."} Garmin stress remains a provider score on its own scale. Symptoms retain their recorded 0–10 severity and use <code>severity × 10</code> only for display position. Missing values remain missing. None of these inputs changes the exposure curve or becomes a dose multiplier, coverage ratio, or physiological requirement.</p>
      <p>That boundary exists because the available evidence describes hydrocortisone pharmacokinetics and stress physiology but does not validate a minute-by-minute conversion from Garmin stress or subjective symptoms to an individual cortisol requirement:</p>
      <ul>{REQUIREMENT_EVIDENCE.map((source) => <li key={source.href}><a href={source.href} target="_blank" rel="noreferrer">{source.label}</a>{` — ${source.use}.`}</li>)}</ul>
      <h3>Overlay display formula</h3>
      <p>For every numeric lane without a fixed domain, let <code>display_min</code> and <code>display_max</code> be its observed selected-day minimum and maximum. The graph uses <code>display = 100 × (value - display_min) / max(display_max - display_min, 1)</code>. An empty lane uses bounds 0 and 1. If every point equals <code>v</code>, the fallback bounds are <code>min(0, v)</code> and <code>v + max(1, abs(v) × 0.1)</code>. Garmin stress uses fixed bounds 0 and 100. Symptoms use fixed bounds 0 and 10, equivalent to <code>severity × 10</code>. Respiration uses a fixed 0–{RESPIRATION_DISPLAY_MAX.toString()} breaths/min display domain and a centered 5-sample median within each observed contiguous segment. Body temperature uses a fixed {TEMPERATURE_DISPLAY_MIN.toString()}–{TEMPERATURE_DISPLAY_MAX.toString()} °F structural display domain and discrete points; conversion uses <code>°F = (°C × 9/5) + 32</code>. Values outside fixed domains are clipped on the graph only. This changes only screen position; exact native values remain in the tooltip and authoritative Timeline. Relative heights are not equivalent measurements and do not establish cortisol need, correlation, or causation.</p>
    </details>
  </Paper>;
}
