import { Button, Checkbox, Group, Paper, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useMemo, useRef, useState } from "react";

import type {
  BloodPressure,
  Episode,
  GarminRecord,
  PhysiologicalCortisolCurve,
  SteroidExposureCurve,
  Symptom,
  Temperature,
  WakeFreeCortisolCurve,
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

export type HealthCurveLaneKey = "exposure" | "stress" | "heart_rate" | "hrv" | "respiration_rate" | "steps" | "blood_pressure" | "temperature" | "symptoms" | "episodes";
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

function doseExclusionReason(reason: SteroidExposureCurve["dose_markers"][number]["exclusion_reason"]): string {
  switch (reason) {
    case "unsupported_medication": return "this medication is not supported by the selected exposure model";
    case "unsupported_formulation": return "this formulation is not supported by the selected exposure model";
    case "unsupported_route": return "this administration route is not supported by the selected exposure model";
    case "unsupported_unit": return "this recorded unit is not supported by the selected exposure model";
    case "unsupported_amount": return "this recorded amount is not supported by the selected exposure model";
    default: return "this recorded dose is not supported by the selected exposure model";
  }
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
  steps: false,
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
  { label: "Steps", keys: ["exposure", "steps"] },
  { label: "Blood pressure", keys: ["exposure", "blood_pressure"] },
  { label: "Temperature", keys: ["exposure", "temperature"] },
  { label: "Recorded events", keys: ["exposure", "symptoms", "episodes"] },
  { label: "All series (busy)", keys: Object.keys(DEFAULT_VISIBLE) as LaneKey[] },
];

const EXPOSURE_REFERENCE_DETAILS: Readonly<Record<string, { label: string; use: string }>> = {
  "https://doi.org/10.1002/j.1552-4604.1991.tb01906.x": { label: "Derendorf et al. (1991)", use: "conventional oral hydrocortisone pharmacokinetics and the 1.7-hour elimination half-life" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC5963674/": { label: "Johnson et al. (2018)", use: "measured oral time-to-peak, bioavailability, and binding-aware variability" },
  "https://doi.org/10.1016/j.metabol.2017.02.005": { label: "Werumeus Buning et al. (2017)", use: "population pharmacokinetics and the large between-person variability motivating a relative rather than clinical-unit curve" },
  "https://doi.org/10.2165/11531290-000000000-00000": { label: "Simon et al. (2010)", use: "one-compartment oral hydrocortisone pharmacokinetics in adrenal insufficiency" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC4880116/": { label: "Endocrine Society guideline (2016)", use: "the short plasma half-life and the distinction between replacement practice and this non-clinical visualization" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC9231005/": { label: "Röhr et al. (2022)", use: "evidence that clinical-unit models require more complex protein-binding and absorption handling" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC7241266/": { label: "Prete et al. (2020)", use: "the fitted total-cortisol exponential after repeated 50 mg intravenous hydrocortisone boluses in primary adrenal insufficiency" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC4280712/": { label: "Jung et al. (2014)", use: "observed total and calculated free cortisol after a 50 mg intravenous Solu-Cortef bolus" },
  "https://www.accessdata.fda.gov/drugsatfda_docs/label/2024/009866s121lbl.pdf": { label: "FDA Solu-Cortef label (2024)", use: "the 100 mg hydrocortisone-equivalent sodium-succinate presentation, intravenous route, and onset context" },
  "https://pmc.ncbi.nlm.nih.gov/articles/PMC4413428/": { label: "Boonen et al. (2015)", use: "documented 100 mg intravenous-bolus use and why critical-illness clearance is not imported as a general personal curve" },
  "https://pubmed.ncbi.nlm.nih.gov/7120045/": { label: "Toothaker et al. (1982)", use: "dose-size-dependent intravenous pharmacokinetics, which is why 100 mg is disclosed as a bounded 2× scenario rather than a separately fitted curve" },
};

const REQUIREMENT_EVIDENCE = [
  { href: "https://pubmed.ncbi.nlm.nih.gov/23506003/", label: "Boonen et al. (2013)", use: "critical illness can reduce cortisol metabolism rather than simply accelerate elimination" },
  { href: "https://pmc.ncbi.nlm.nih.gov/articles/PMC7241266/", label: "Prete et al. (2020)", use: "major-stress cortisol delivery differs by administration method and cannot be inferred from a consumer stress score" },
  { href: "https://pmc.ncbi.nlm.nih.gov/articles/PMC3813945/", label: "Lewis and Elder (2013)", use: "cortisol-binding globulin materially affects total and free cortisol interpretation" },
] as const;

function isAbsoluteCortisolCurve(exposure: SteroidExposureCurve): exposure is Extract<SteroidExposureCurve, { series_unit: "nmol/L" }> {
  return exposure.series_unit === "nmol/L";
}

function isWakeFreeCurve(exposure: SteroidExposureCurve): exposure is WakeFreeCortisolCurve {
  return "series_kind" in exposure
    && exposure.series_kind === "modeled_serum_free_cortisol_scenario";
}

function isMixedRouteCurve(exposure: SteroidExposureCurve): boolean {
  return isWakeFreeCurve(exposure) && exposure.model.id === "hc-mixed-route-free-v4";
}

function supportedDoseDescription(exposure: WakeFreeCortisolCurve): string {
  const medications = exposure.model.supported_medications ?? [exposure.model.supported_medication];
  const formulations = exposure.model.supported_formulations ?? [exposure.model.supported_formulation];
  const routes = exposure.model.supported_routes ?? [exposure.model.supported_route];
  return `${medications.join(" or ")}; ${formulations.join(" or ")}; ${routes.join(" or ")}`;
}

function isPhysiologicalCurve(exposure: SteroidExposureCurve): exposure is PhysiologicalCortisolCurve {
  return "series_kind" in exposure
    && exposure.series_kind === "modeled_plasma_free_cortisol_scenario";
}

function exposureModelVersion(exposure: SteroidExposureCurve): string {
  return isAbsoluteCortisolCurve(exposure) ? exposure.model.revision : exposure.model.version;
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
  { key: "steps", metric: "steps", label: "Hourly steps", unit: "steps" },
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

function relativeValue(
  lane: Lane,
  value: number,
  overrideBounds?: { minimum: number; maximum: number },
): number {
  const bounds = overrideBounds ?? (lane.key === "stress" ? { minimum: 0, maximum: 100 }
    : lane.key === "symptoms" ? { minimum: 0, maximum: 10 }
      : lane.key === "respiration_rate" ? { minimum: RESPIRATION_DISPLAY_MIN, maximum: RESPIRATION_DISPLAY_MAX }
        : lane.key === "temperature" ? { minimum: TEMPERATURE_DISPLAY_MIN, maximum: TEMPERATURE_DISPLAY_MAX }
      : scale(lane.points.map((point) => point.value)));
  const relative = (value - bounds.minimum) / Math.max(bounds.maximum - bounds.minimum, 1) * 100;
  return ["respiration_rate", "temperature"].includes(lane.key) ? Math.max(0, Math.min(100, relative)) : relative;
}

function yPosition(lane: Lane, value: number, overrideBounds?: { minimum: number; maximum: number }): number {
  return TOP + PLOT_HEIGHT - relativeValue(lane, value, overrideBounds) / 100 * PLOT_HEIGHT;
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

function path(
  lane: Lane,
  points: Point[],
  start: number,
  end: number,
  overrideBounds?: { minimum: number; maximum: number },
): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${xPosition(point.time, start, end).toFixed(2)} ${yPosition(lane, point.value, overrideBounds).toFixed(2)}`).join(" ");
}

function contextBandPath(
  lane: Lane,
  samples: SteroidExposureCurve["context_band"]["samples"],
  start: number,
  end: number,
  bounds: { minimum: number; maximum: number },
): string {
  if (samples.length < 2) return "";
  const upper = samples.map((sample, index) => `${index === 0 ? "M" : "L"} ${xPosition(sample.occurred_at, start, end).toFixed(2)} ${yPosition(lane, Number(sample.upper_nmol_l), bounds).toFixed(2)}`);
  const lower = [...samples].reverse().map((sample) => `L ${xPosition(sample.occurred_at, start, end).toFixed(2)} ${yPosition(lane, Number(sample.lower_nmol_l), bounds).toFixed(2)}`);
  return [...upper, ...lower, "Z"].join(" ");
}

function wakeReferenceBandPath(
  lane: Lane,
  samples: WakeFreeCortisolCurve["wake_reference"]["samples"],
  start: number,
  end: number,
  bounds: { minimum: number; maximum: number },
): string {
  if (samples.length < 2) return "";
  const upper = samples.map((sample, index) => `${index === 0 ? "M" : "L"} ${xPosition(sample.occurred_at, start, end).toFixed(2)} ${yPosition(lane, Number(sample.serum_free_p95_nmol_l), bounds).toFixed(2)}`);
  const lower = [...samples].reverse().map((sample) => `L ${xPosition(sample.occurred_at, start, end).toFixed(2)} ${yPosition(lane, Number(sample.serum_free_p5_nmol_l), bounds).toFixed(2)}`);
  return [...upper, ...lower, "Z"].join(" ");
}

function wakeReferenceMedianPath(
  lane: Lane,
  samples: WakeFreeCortisolCurve["wake_reference"]["samples"],
  start: number,
  end: number,
  bounds: { minimum: number; maximum: number },
): string {
  return samples.map((sample, index) => `${index === 0 ? "M" : "L"} ${xPosition(sample.occurred_at, start, end).toFixed(2)} ${yPosition(lane, Number(sample.serum_free_p50_nmol_l), bounds).toFixed(2)}`).join(" ");
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
    label: isAbsoluteCortisolCurve(data.exposure) ? data.exposure.series_name : "Theoretical exposure",
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
        series: `${dose.category === "stress" ? "Stress dose" : "Regular dose"}${dose.supported ? "" : " (not modeled)"}`,
        value: `${dose.medication_name} ${formatMeasurement(dose.amount, dose.unit)}${dose.supported ? "" : ` — ${doseExclusionReason(dose.exclusion_reason)}`}`,
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
  const [showContextBand, setShowContextBand] = useState(false);
  const [showWakeReferenceBand, setShowWakeReferenceBand] = useState(true);
  const activeTouchPointer = useRef<number | null>(null);
  const cursorTime = Math.min(end, start + cursorMinute * 60_000);
  const allLanes = useMemo(() => lanes(data), [data]);
  const shownLanes = allLanes.filter((lane) => visible[lane.key]);
  const stepsUnavailable = visible.steps
    && allLanes.find((lane) => lane.key === "steps")?.points.length === 0;
  const exposureLane = allLanes.find((lane) => lane.key === "exposure");
  const contextBand = data.exposure.context_band;
  const visibleContextBand = showContextBand;
  const wakeReference = isWakeFreeCurve(data.exposure) ? data.exposure.wake_reference : undefined;
  const coverageFeatures = isWakeFreeCurve(data.exposure) ? data.exposure.coverage_features : undefined;
  const visibleWakeReferenceBand = wakeReference?.available === true && showWakeReferenceBand;
  const wakeAbsoluteBounds = !isWakeFreeCurve(data.exposure) || exposureLane === undefined
    ? undefined
    : {
      minimum: 0,
      maximum: Math.max(
        1,
        ...exposureLane.points.map((point) => point.value),
        ...data.exposure.wake_reference.samples.map((sample) => Number(sample.serum_free_p95_nmol_l)),
        ...(visibleContextBand
          ? contextBand.samples.map((sample) => Number(sample.upper_nmol_l))
          : []),
      ),
    };
  const contextBandBounds = exposureLane === undefined || !visibleContextBand
    ? undefined
    : wakeAbsoluteBounds ?? scale([
      ...(isAbsoluteCortisolCurve(data.exposure)
        ? exposureLane.points.map((point) => point.value)
        : []),
      ...contextBand.samples.flatMap((sample) => [Number(sample.lower_nmol_l), Number(sample.upper_nmol_l)]),
    ]);
  const exposureContextBounds = wakeAbsoluteBounds
    ?? (isAbsoluteCortisolCurve(data.exposure) ? contextBandBounds : undefined);
  const ticks = timeTicks(start, end, data.exposure.timezone);
  const expectedPreWakeEnd = isWakeFreeCurve(data.exposure)
    && data.exposure.wake_reference.available
    && data.exposure.wake_reference.assumptions != null
    ? Date.parse(data.exposure.wake_reference.assumptions.wake_at)
    : undefined;
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
  const unmodeledDoses = data.exposure.dose_markers.filter((dose) => {
    const occurredAt = Date.parse(dose.occurred_at);
    return !dose.supported && occurredAt >= start && occurredAt < end;
  });
  const cursorDoseObservations = visible.exposure
    ? nearbyTooltipObservations(doseObservations, cursorTime)
    : [];
  const cursorPoints = nearestVisiblePoints(shownLanes, cursorTime);
  const cursorExposurePoint = cursorPoints.find(({ lane }) => lane.key === "exposure")?.point;
  const cursorExposureSample = cursorExposurePoint === undefined
    ? undefined
    : data.exposure.samples.find((sample) => sample.occurred_at === cursorExposurePoint.time);
  const nearestContextBandSample = !visibleContextBand
    ? undefined
    : contextBand.samples.reduce<typeof contextBand.samples[number] | undefined>((nearest, sample) => {
      if (nearest === undefined) return sample;
      return Math.abs(Date.parse(sample.occurred_at) - cursorTime) < Math.abs(Date.parse(nearest.occurred_at) - cursorTime)
        ? sample
        : nearest;
    }, undefined);
  const cursorContextBandSample = nearestContextBandSample === undefined
    || Math.abs(Date.parse(nearestContextBandSample.occurred_at) - cursorTime) > data.exposure.model.sample_interval_minutes * 30_000
    ? undefined
    : nearestContextBandSample;
  const nearestWakeReferenceSample = !visibleWakeReferenceBand
    ? undefined
    : wakeReference.samples.reduce<typeof wakeReference.samples[number] | undefined>((nearest, sample) => {
      if (nearest === undefined) return sample;
      return Math.abs(Date.parse(sample.occurred_at) - cursorTime) < Math.abs(Date.parse(nearest.occurred_at) - cursorTime)
        ? sample
        : nearest;
    }, undefined);
  const cursorWakeReferenceSample = nearestWakeReferenceSample === undefined
    || Math.abs(Date.parse(nearestWakeReferenceSample.occurred_at) - cursorTime) > data.exposure.model.sample_interval_minutes * 30_000
    ? undefined
    : nearestWakeReferenceSample;
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
    ...(cursorContextBandSample === undefined ? [] : [{
      key: `context-band-${cursorContextBandSample.occurred_at}`,
      series: "Illustrative circadian context",
      value: `${Number(cursorContextBandSample.lower_nmol_l).toFixed(1)}–${Number(cursorContextBandSample.upper_nmol_l).toFixed(1)} nmol/L (center ${Number(cursorContextBandSample.center_nmol_l).toFixed(1)})`,
    }]),
    ...(cursorWakeReferenceSample === undefined ? [] : [{
      key: `wake-reference-${cursorWakeReferenceSample.occurred_at}`,
      series: "Wake-anchored healthy reference",
      value: `P5 ${Number(cursorWakeReferenceSample.serum_free_p5_nmol_l).toFixed(1)} · median ${Number(cursorWakeReferenceSample.serum_free_p50_nmol_l).toFixed(1)} · P95 ${Number(cursorWakeReferenceSample.serum_free_p95_nmol_l).toFixed(1)} nmol/L free`,
    }]),
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
    {isWakeFreeCurve(data.exposure) ? <Text size="sm" c="dimmed">The modeled and healthy-reference curves are estimates for review, not measurements, medication-adequacy tests, alerts, or dosing guidance. Recorded symptoms and physician-authored instructions take precedence.</Text> : null}
    <dl className="metric-metadata"><div><dt>Selected date</dt><dd className="healthcurve-selected-day">{onPreviousDay === undefined ? null : <button type="button" className="button-secondary" aria-label="Review previous day" onClick={onPreviousDay}>←</button>}<span>{data.exposure.date}</span>{onNextDay === undefined ? null : <button type="button" className="button-secondary" aria-label="Review next day" disabled={nextDayDisabled} onClick={onNextDay}>→</button>}</dd></div><div><dt>Timezone</dt><dd>{timezoneAbbreviationForLocalDate(data.exposure.timezone, data.exposure.date)}</dd></div><div><dt>Elapsed day</dt><dd>{formatDecimal(data.exposure.elapsed_hours)} hours</dd></div></dl>
    <details className="metric-definition healthcurve-context">
      <summary>HealthCurve context and limits</summary>
      <div className="healthcurve-context-content">
        <dl className="metric-metadata healthcurve-context-model"><div><dt>Exposure model</dt><dd>{exposureModelVersion(data.exposure)}</dd></div></dl>
        <aside className="association-caution"><strong>Association does not establish causation.</strong> These summaries describe the selected records. They do not determine why a symptom, dose, or episode occurred and are not medical advice.</aside>
        <aside className="association-caution"><strong>Focused comparison on one time axis.</strong> The graph starts with theoretical exposure and Garmin stress so the shape stays readable. Choose another focus below or opt into the deliberately busy all-series view. {isWakeFreeCurve(data.exposure) ? "The modeled and wake-anchored reference cortisol series share one absolute serum-free-cortisol axis in nmol/L and are never independently normalized. Other enabled health series use a separate relative 0–100 display position." : "Every enabled series uses a relative 0–100 display scale."} Exact values keep their original units in the hover tooltip and authoritative Timeline. Relative heights are not equivalent measurements, do not establish causation, do not measure cortisol, and do not determine medication need.</aside>
        <p className="curve-missingness"><strong>Missingness:</strong> Garmin cadence is observational, so expected missing counts are not invented. Lines connect only contiguous samples with an observed cadence. Unknown or interrupted intervals remain blank; no interpolated values are stored as facts.{missingWakeTiming ? " Garmin reported one or more awakenings without their exact times, so no intermediate wake markers are invented for those sessions." : ""}</p>
        {!isWakeFreeCurve(data.exposure) ? null : data.exposure.wake_reference.available && data.exposure.wake_reference.assumptions != null ? <div>
          <p><strong>Wake-anchored reference inputs:</strong> final wake {experiencedTime(data.exposure.wake_reference.assumptions.wake_at, data.exposure.timezone)}; sleep onset {experiencedTime(data.exposure.wake_reference.assumptions.sleep_onset_at, data.exposure.timezone)}.</p>
          <p><strong>Observed meal assumptions:</strong> {Object.entries(data.exposure.wake_reference.assumptions.observed_meals).length === 0 ? "No meals were recorded, so no meal bumps were added." : Object.entries(data.exposure.wake_reference.assumptions.observed_meals).map(([role, occurredAt]) => `${role} ${experiencedTime(occurredAt, data.exposure.timezone)}`).join("; ")}. Unobserved meals are never invented.</p>
        </div> : <p role="status"><strong>Wake-anchored reference unavailable:</strong> {data.exposure.wake_reference.missing_inputs.map((input) => input === "wake_at" ? "final wake time" : "sleep onset time").join(" and ")} {data.exposure.wake_reference.missing_inputs.length === 1 ? "is" : "are"} missing from current Garmin sleep facts. The modeled dose curve remains available; HealthCurve does not invent the missing timing.</p>}
      </div>
    </details>
    <Stack className="healthcurve-controls" gap="sm" aria-label="HealthCurve chart controls"><Group className="healthcurve-focus" gap="xs" role="group" aria-label="Choose a focused HealthCurve comparison"><Text fw={750}>Quick focus:</Text>{FOCUS_PRESETS.map((preset) => <Button key={preset.label} type="button" size="sm" variant={isPresetVisible(visible, preset.keys) ? "filled" : "outline"} aria-pressed={isPresetVisible(visible, preset.keys)} onClick={() => { setVisible(presetVisibility(preset.keys)); }}>{preset.label}</Button>)}</Group>
      <Paper component="fieldset" className="curve-toggles" withBorder radius="md" p="md"><legend>Show or hide chart series</legend><SimpleGrid cols={{ base: 1, xs: 2, md: 3 }}>{Object.entries({
        exposure: isMixedRouteCurve(data.exposure) ? "Full cortisol model (v4) and actual doses" : isWakeFreeCurve(data.exposure) ? "Wake-anchored oral free-cortisol model and actual doses" : isAbsoluteCortisolCurve(data.exposure) ? "Physiological scenario and actual doses" : "Theoretical exposure and actual doses",
        stress: "Garmin stress",
        heart_rate: "Heart rate",
        hrv: "HRV",
        respiration_rate: "Respiration",
        steps: "Steps",
        blood_pressure: "Blood pressure",
        temperature: "Temperature",
        symptoms: "Symptoms",
        episodes: "Stress episodes",
      } satisfies Record<LaneKey, string>).map(([key, label]) => <Checkbox key={key} label={label} checked={visible[key as LaneKey]} onChange={(event) => { setVisible({ ...visible, [key]: event.target.checked }); }} />)}<Checkbox label="Illustrative circadian context band" description="Population-shape context only; not a personal target or adequacy range." checked={showContextBand} onChange={(event) => { setShowContextBand(event.target.checked); }} />{isWakeFreeCurve(data.exposure) ? <Checkbox label="Wake-anchored healthy P5–P95 reference" description="Healthy-adult context regenerated from observed wake, sleep, and meals; not a personal target." checked={showWakeReferenceBand} disabled={!data.exposure.wake_reference.available} onChange={(event) => { setShowWakeReferenceBand(event.target.checked); }} /> : <Checkbox label="Wake-anchored healthy P5–P95 reference" description="Available with the wake-anchored v3 and mixed-route v4 exposure models." checked={false} disabled />}</SimpleGrid>
      {stepsUnavailable ? <Text role="status" c="dimmed">Hourly Steps are unavailable for this day because Garmin supplied no observed intraday step samples. The untimed daily step total is not drawn at an invented time.</Text> : null}
    </Paper></Stack>
    {unmodeledDoses.length === 0 ? null : <aside className="healthcurve-unmodeled-doses" role="status">
      <strong>{unmodeledDoses.length.toString()} recorded {unmodeledDoses.length === 1 ? "dose is" : "doses are"} shown but not modeled.</strong>{" "}
      These are actual recorded facts and do not require a dose plan. The selected model supports only its listed medications, formulations, routes, amounts, and units{isWakeFreeCurve(data.exposure) ? ` (${supportedDoseDescription(data.exposure)})` : ""}; it does not invent exposure for an unsupported fact. Hollow diamond markers show the recorded administration times.
      <ul>{unmodeledDoses.map((dose) => <li key={dose.dose_event_id}><time dateTime={dose.occurred_at}>{experiencedTime(dose.occurred_at, data.exposure.timezone)}</time>: <strong>{dose.category === "stress" ? "Stress dose" : "Dose"} — {dose.medication_name} {formatMeasurement(dose.amount, dose.unit)}</strong> ({dose.route}; {doseExclusionReason(dose.exclusion_reason)})</li>)}</ul>
    </aside>}
    <div className="healthcurve-legend" aria-label="Overlay series legend">{sleepRecords.length === 0 ? null : <><span><i className="healthcurve-key healthcurve-key--sleep" aria-hidden="true" />Sleep session</span><span><i className="healthcurve-key healthcurve-key--awake" aria-hidden="true" />Explicit awake interval</span></>}{visibleContextBand ? <span><i className="healthcurve-key healthcurve-key--context-band" aria-hidden="true" />Illustrative circadian context · nmol/L</span> : null}{visibleWakeReferenceBand ? <span><i className="healthcurve-key healthcurve-key--wake-reference" aria-hidden="true" />Wake-anchored healthy reference · P5–P95 free nmol/L</span> : null}{shownLanes.map((lane) => <span key={lane.key}><i className={`healthcurve-key healthcurve-key--${lane.key}`} aria-hidden="true" />{lane.label} · {lane.unit}{lane.key === "respiration_rate" ? " · calmer 5-sample median line" : ""}</span>)}</div>
    <div className="healthcurve-mobile-controls" role="group" aria-label="Mobile chart controls">
      <span>Chart zoom</span>
      <button type="button" className="button-secondary" aria-label="Zoom chart out" disabled={chartZoom === 1} onClick={() => { setChartZoom(chartZoom === 2 ? 1.5 : 1); }}>−</button>
      <output aria-live="polite">{chartZoom.toString()}×</output>
      <button type="button" className="button-secondary" aria-label="Zoom chart in" disabled={chartZoom === 2} onClick={() => { setChartZoom(chartZoom === 1 ? 1.5 : 2); }}>+</button>
    </div>
    <div className="healthcurve-scroll" tabIndex={0} role="region" aria-label="Daily HealthCurve synchronized chart">
      <svg className={`healthcurve-chart healthcurve-chart--zoom-${chartZoom.toString().replace(".", "-")}`} viewBox={`0 0 ${WIDTH.toString()} ${HEIGHT.toString()}`} role="img" aria-label={`Interactive selected-day HealthCurve overlay for ${data.exposure.date} in ${timezoneAbbreviationForLocalDate(data.exposure.timezone, data.exposure.date)}; ${data.symptoms.length.toString()} recorded symptom ${data.symptoms.length === 1 ? "event" : "events"}; ${isWakeFreeCurve(data.exposure) ? "modeled and reference cortisol share an absolute serum-free-cortisol axis while other series use relative display positions" : "relative display positions share one time axis"}, and exact values follow.`}>
        <rect className="healthcurve-overlay-bg" x={LEFT} y={TOP} width={PLOT_WIDTH} height={PLOT_HEIGHT} />
        {expectedPreWakeEnd === undefined || expectedPreWakeEnd <= start ? null : <g data-series="expected-pre-wake-gap">
          <rect className="healthcurve-expected-pre-wake" x={LEFT} y={TOP} width={Math.max(0, Math.min(PLOT_WIDTH, (expectedPreWakeEnd - start) / Math.max(end - start, 1) * PLOT_WIDTH))} height={PLOT_HEIGHT} />
          <text className="healthcurve-expected-pre-wake-label" x={LEFT + 8} y={TOP + 47}>Expected oral pre-wake gap</text>
        </g>}
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
          const cortisolLabel = wakeAbsoluteBounds === undefined
            ? relative.toString()
            : summaryNumber(wakeAbsoluteBounds.minimum + relative / 100 * (wakeAbsoluteBounds.maximum - wakeAbsoluteBounds.minimum), 1);
          return <g key={relative}><line className="healthcurve-relative-grid" x1={LEFT} y1={y} x2={LEFT + PLOT_WIDTH} y2={y} /><text className="healthcurve-scale-label" x={LEFT - 10} y={y} dy="0.35em" textAnchor="end">{cortisolLabel}</text>{wakeAbsoluteBounds === undefined ? null : <text className="healthcurve-scale-label healthcurve-secondary-scale-label" x={LEFT + PLOT_WIDTH + 7} y={y} dy="0.35em" textAnchor="start">{relative.toString()}</text>}</g>;
        })}
        {ticks.map((tick) => {
          const x = LEFT + (tick.time - start) / Math.max(end - start, 1) * PLOT_WIDTH;
          return <g key={tick.time} className={tick.label === null ? "healthcurve-hour-tick" : "healthcurve-major-time-tick"}>
            {tick.label === null ? null : <line className="healthcurve-time-grid" x1={x} y1={TOP} x2={x} y2={TOP + PLOT_HEIGHT} />}
            <line className="healthcurve-hour-mark" x1={x} y1={TOP + PLOT_HEIGHT} x2={x} y2={TOP + PLOT_HEIGHT + (tick.label === null ? 5 : 8)} />
            {tick.label === null ? null : <text className="healthcurve-time-label" x={x} y={HEIGHT - 34} textAnchor={tick.time === start ? "start" : tick.time === end ? "end" : "middle"}>{tick.label}</text>}
          </g>;
        })}
        {!visibleContextBand || exposureLane === undefined || contextBandBounds === undefined ? null : <g data-series="context-band" aria-label={contextBand.safety_label}>
          <path className="healthcurve-context-band" d={contextBandPath(exposureLane, contextBand.samples, start, end, contextBandBounds)} />
        </g>}
        {!visibleWakeReferenceBand || exposureLane === undefined || wakeAbsoluteBounds === undefined ? null : <g data-series="wake-reference-band" aria-label={wakeReference.safety_label}>
          <path className="healthcurve-wake-reference-band" d={wakeReferenceBandPath(exposureLane, wakeReference.samples, start, end, wakeAbsoluteBounds)} />
          <path className="healthcurve-wake-reference-median" d={wakeReferenceMedianPath(exposureLane, wakeReference.samples, start, end, wakeAbsoluteBounds)} />
        </g>}
        {visible.episodes ? <g data-series="episodes">{data.episodes.map((episode) => {
            const x = Math.max(LEFT, Math.min(LEFT + PLOT_WIDTH, xPosition(episode.started_at, start, end)));
            const xEnd = Math.max(LEFT, Math.min(LEFT + PLOT_WIDTH, xPosition(episode.ended_at ?? data.exposure.day_end, start, end)));
            return <rect key={episode.id} className="healthcurve-episode" x={x} y={TOP} width={Math.max(4, xEnd - x)} height={PLOT_HEIGHT}><title>{experiencedTime(episode.started_at, data.exposure.timezone)}: {episode.trigger}; {episode.severity ?? "severity missing"}; {episode.status}</title></rect>;
          })}</g> : null}
        {shownLanes.map((lane) => <g key={lane.key} data-series={lane.key}>
          {connectedSegments(lane).map((segment, index) => <path key={`${lane.key}-${index.toString()}`} className={`healthcurve-series healthcurve-series--${lane.key}${lane.key === "exposure" ? " healthcurve-exposure-line" : ""}`} d={path(lane, displaySegment(lane, segment), start, end, lane.key === "exposure" ? exposureContextBounds : undefined)} />)}
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
          const title = `${experiencedTime(dose.occurred_at, data.exposure.timezone)}: recorded ${dose.category === "stress" ? "stress " : ""}dose ${formatMeasurement(dose.amount, dose.unit)} ${dose.medication_name}${dose.supported ? "" : `; not modeled because ${doseExclusionReason(dose.exclusion_reason)}`}`;
          return dose.supported
            ? <g key={dose.dose_event_id}><line className="healthcurve-dose-marker" x1={x} y1={TOP} x2={x} y2={TOP + PLOT_HEIGHT}><title>{title}</title></line><circle className="healthcurve-dose-dot" cx={x} cy={TOP} r="5" /></g>
            : <g key={dose.dose_event_id} className="healthcurve-unmodeled-dose"><line className="healthcurve-dose-marker healthcurve-dose-marker--unmodeled" x1={x} y1={TOP} x2={x} y2={TOP + PLOT_HEIGHT}><title>{title}</title></line><polygon className="healthcurve-dose-dot healthcurve-dose-dot--unmodeled" points={`${(x - 6).toString()},${TOP.toString()} ${x.toString()},${(TOP - 6).toString()} ${(x + 6).toString()},${TOP.toString()} ${x.toString()},${(TOP + 6).toString()}`} /></g>;
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
        <text transform={`translate(18 ${String(TOP + PLOT_HEIGHT / 2)}) rotate(-90)`} textAnchor="middle" className="healthcurve-axis-title">{wakeAbsoluteBounds === undefined ? "Relative display position (0–100)" : "Serum free cortisol (nmol/L)"}</text>
        {wakeAbsoluteBounds === undefined ? null : <text transform={`translate(${String(WIDTH - 4)} ${String(TOP + PLOT_HEIGHT / 2)}) rotate(90)`} textAnchor="middle" className="healthcurve-axis-title healthcurve-secondary-axis-title">Other series relative (0–100)</text>}
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
      const hasRecordedValues = aggregates.length > 0
        || values.length > 0
        || (lane.key === "blood_pressure" && data.bloodPressure.length > 0)
        || (lane.key === "temperature" && data.temperature.length > 0);
      const metadataId = `curve-summary-metadata-${lane.key}`;
      return <section key={lane.key} className="curve-summary-card" aria-labelledby={`curve-summary-title-${lane.key}`}>
        <h3 id={`curve-summary-title-${lane.key}`}>{lane.label}<SummaryInfo id={metadataId} label={lane.label}>{laneMetadata(lane, data, unscoredSymptoms.length)}</SummaryInfo></h3>
        <div className="curve-summary-values">
          {aggregates.map((record) => <p key={record.id}><strong>{record.measurement_label ?? garminMetricLabel(record.metric_type)}:</strong> {dailyAggregateValue(record)}</p>)}
          {lane.key === "exposure" ? <p><strong>Peak:</strong> {summaryNumber(Math.max(...values, 0), 3)} {data.exposure.series_unit}</p> : null}
          {["stress", "heart_rate", "hrv", "respiration_rate"].includes(lane.key) && average !== null ? <><p><strong>Observed average:</strong> {summaryNumber(average)} {lane.unit}</p><p><strong>Observed range:</strong> {summaryNumber(Math.min(...values))}–{summaryNumber(Math.max(...values))} {lane.unit}</p></> : null}
          {lane.key === "steps" && values.length > 0 ? <><p><strong>Observed total:</strong> {summaryNumber(values.reduce((total, value) => total + value, 0), 0)} steps</p><p><strong>Hourly range:</strong> {summaryNumber(Math.min(...values), 0)}–{summaryNumber(Math.max(...values), 0)} steps</p></> : null}
          {lane.key === "blood_pressure" ? data.bloodPressure.map((record) => <p key={record.id}><strong>{record.systolic_mmhg.toString()}/{record.diastolic_mmhg.toString()} mmHg</strong>{record.pulse_bpm == null ? null : ` · pulse ${record.pulse_bpm.toString()} bpm`}</p>) : null}
          {lane.key === "temperature" ? data.temperature.map((record) => <p key={record.id}><strong>{record.display_f} °F</strong> ({record.display_c} °C)</p>) : null}
          {lane.key === "symptoms" ? data.symptoms.map((record) => <p key={record.id}><strong>{record.name}</strong>{record.severity == null ? " · severity not recorded" : ` · ${record.severity.toString()}/10`}</p>) : null}
          {!hasRecordedValues && lane.key !== "symptoms" ? <p className="muted">No values recorded.</p> : null}
          {lane.key === "symptoms" && data.symptoms.length === 0 ? <p className="muted">No symptoms recorded.</p> : null}
        </div>
      </section>;
    })}</div>
    {coverageFeatures === undefined ? null : <details className="metric-definition wake-coverage-features">
      <summary>Deterministic cortisol comparison features</summary>
      <p><strong>{coverageFeatures.safety_label}</strong></p>
      {!coverageFeatures.available || coverageFeatures.auc == null ? <p role="status">These comparisons are unavailable because {coverageFeatures.missing_inputs.join(" and ") || "the aligned modeled and reference window is unavailable"}. Missing inputs remain missing.</p> : <>
        <dl className="metric-metadata">
          <div><dt>Analysis window</dt><dd>{formatDecimal(coverageFeatures.elapsed_hours)} elapsed hours · {coverageFeatures.day_state}</dd></div>
          <div><dt>Neutral comparison window</dt><dd>{formatDecimal(coverageFeatures.comparison_minutes ?? "0")} minutes</dd></div>
          <div><dt>Expected sleep / pre-dose time excluded</dt><dd>{formatDecimal(coverageFeatures.expected_pre_wake_excluded_minutes ?? "0")} minutes</dd></div>
          <div><dt>Below healthy-reference P5</dt><dd>{formatDecimal(coverageFeatures.time_below_p5_minutes ?? "0")} minutes</dd></div>
          <div><dt>Below healthy-reference P25</dt><dd>{formatDecimal(coverageFeatures.time_below_p25_minutes ?? "0")} minutes</dd></div>
          <div><dt>Modeled free-cortisol AUC</dt><dd>{formatDecimal(coverageFeatures.auc.modeled_free_nmol_l_hours)} nmol/L-hours</dd></div>
          <div><dt>Healthy-reference median AUC</dt><dd>{formatDecimal(coverageFeatures.auc.reference_p50_nmol_l_hours)} nmol/L-hours</dd></div>
          <div><dt>Regular / stress-dose AUC</dt><dd>{formatDecimal(coverageFeatures.auc.regular_modeled_free_nmol_l_hours)} / {formatDecimal(coverageFeatures.auc.stress_modeled_free_nmol_l_hours)} nmol/L-hours</dd></div>
          <div><dt>Time above healthy-reference P95</dt><dd>{formatDecimal(coverageFeatures.p95_overshoot?.duration_minutes ?? "0")} minutes</dd></div>
          <div><dt>Maximum fall rate</dt><dd>{coverageFeatures.maximum_fall == null ? "No falling interval" : `${formatDecimal(coverageFeatures.maximum_fall.magnitude_nmol_l_per_hour)} nmol/L per hour at ${experiencedTime(coverageFeatures.maximum_fall.interval_started_at, data.exposure.timezone)}`}</dd></div>
        </dl>
        <p>Expected overnight and pre-first-dose differences are excluded from below-band time rather than treated as anomalies. Percentile position is population context only.</p>
        {coverageFeatures.inter_dose_troughs.length === 0 ? <p>No inter-dose trough was available for this day.</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label="Inter-dose cortisol troughs"><table><caption>Modeled minima between consecutive supported recorded doses.</caption><thead><tr><th scope="col">Time</th><th scope="col">Modeled free</th><th scope="col">Regular</th><th scope="col">Stress dose</th><th scope="col">Reference median</th></tr></thead><tbody>{coverageFeatures.inter_dose_troughs.map((trough) => <tr key={`${trough.previous_dose_event_id}-${trough.next_dose_event_id}`}><td>{experiencedTime(trough.occurred_at, data.exposure.timezone)}</td><td>{formatMeasurement(trough.modeled_free_cortisol_nmol_l, "nmol/L")}</td><td>{formatMeasurement(trough.regular_modeled_free_cortisol_nmol_l, "nmol/L")}</td><td>{formatMeasurement(trough.stress_modeled_free_cortisol_nmol_l, "nmol/L")}</td><td>{formatMeasurement(trough.reference_p50_nmol_l, "nmol/L")}</td></tr>)}</tbody></table></div>}
        <p>{coverageFeatures.uncategorized_symptom_count === 0 ? "Every symptom in this analysis window has an owner-selected tracking category." : `${coverageFeatures.uncategorized_symptom_count.toString()} symptom ${coverageFeatures.uncategorized_symptom_count === 1 ? "has" : "have"} no tracking category; HealthCurve leaves ${coverageFeatures.uncategorized_symptom_count === 1 ? "it" : "them"} uncategorized rather than guessing.`}</p>
        {coverageFeatures.symptom_contexts.length === 0 ? <p>No recorded symptom had aligned modeled/reference context in this analysis window.</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label="Symptom cortisol timing context"><table><caption>Temporal context at recorded symptoms; categories are owner-selected context and proximity does not establish causation.</caption><thead><tr><th scope="col">Symptom and time</th><th scope="col">Tracking category</th><th scope="col">Time since supported dose</th><th scope="col">Modeled free</th><th scope="col">Reference P5–P95</th></tr></thead><tbody>{coverageFeatures.symptom_contexts.map((symptom) => <tr key={symptom.symptom_event_id}><td><strong>{symptom.name}</strong><br />{experiencedTime(symptom.occurred_at, data.exposure.timezone)}</td><td>{symptom.tracking_category == null ? "Not categorized" : symptom.tracking_category.replaceAll("_", " ")}</td><td>{symptom.minutes_since_previous_supported_dose == null ? "No previous supported dose" : `${formatDecimal(symptom.minutes_since_previous_supported_dose)} minutes`}</td><td>{formatMeasurement(symptom.modeled_free_cortisol_nmol_l, "nmol/L")}</td><td>{formatDecimal(symptom.reference_p5_nmol_l)}–{formatDecimal(symptom.reference_p95_nmol_l)} nmol/L</td></tr>)}</tbody></table></div>}
      </>}
      <p className="muted">Feature version {coverageFeatures.feature_revision}; source fingerprint {coverageFeatures.source_revision_sha256.slice(0, 12)}…</p>
    </details>}
    {wakeReference === undefined ? null : <details className="metric-definition wake-reference-values">
      <summary>Wake-anchored healthy reference values and assumptions</summary>
      <p><strong>{wakeReference.safety_label}</strong> P5–P95 describes a wide healthy-adult population reference, not a personal target, alert, medication-adequacy test, or dosing guide.</p>
      {!wakeReference.available || wakeReference.assumptions == null ? <p>No reference values were generated because {wakeReference.missing_inputs.map((input) => input === "wake_at" ? "final wake time" : "sleep onset time").join(" and ")} {wakeReference.missing_inputs.length === 1 ? "was" : "were"} unavailable. Missing timing remains missing.</p> : <>
        <p>The band uses observed final wake at {experiencedTime(wakeReference.assumptions.wake_at, data.exposure.timezone)}, observed sleep onset at {experiencedTime(wakeReference.assumptions.sleep_onset_at, data.exposure.timezone)}, age {formatDecimal(wakeReference.assumptions.age_years)}, sex {wakeReference.assumptions.sex}, and {Object.keys(wakeReference.assumptions.observed_meals).length.toString()} observed meal {Object.keys(wakeReference.assumptions.observed_meals).length === 1 ? "time" : "times"}. Unrecorded meals are not invented.</p>
        <p>The pre-wake difference between this healthy reference and oral-dose model is neutrally expected because immediate-release oral hydrocortisone cannot create a rise before the first dose is taken.</p>
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Wake-anchored cortisol reference exact values"><table><caption>Exact serum-free and derived serum-total reference percentiles supplied by {wakeReference.reference.revision}. Model and reference samples share identical timestamps.</caption><thead><tr><th scope="col">Local time</th><th scope="col">Free P5</th><th scope="col">Free median</th><th scope="col">Free P95</th><th scope="col">Total P5</th><th scope="col">Total median</th><th scope="col">Total P95</th></tr></thead><tbody>{wakeReference.samples.map((sample) => <tr key={sample.occurred_at}><td>{experiencedTime(sample.occurred_at, data.exposure.timezone)}</td><td>{formatMeasurement(sample.serum_free_p5_nmol_l, "nmol/L")}</td><td>{formatMeasurement(sample.serum_free_p50_nmol_l, "nmol/L")}</td><td>{formatMeasurement(sample.serum_free_p95_nmol_l, "nmol/L")}</td><td>{formatMeasurement(sample.serum_total_p5_nmol_l, "nmol/L")}</td><td>{formatMeasurement(sample.serum_total_p50_nmol_l, "nmol/L")}</td><td>{formatMeasurement(sample.serum_total_p95_nmol_l, "nmol/L")}</td></tr>)}</tbody></table></div>
      </>}
    </details>}
    <details className="metric-definition context-band-values">
      <summary>Illustrative circadian context band values</summary>
      <p><strong>{contextBand.safety_label}</strong> This population-shape context is not a personal target, measured cortisol range, medication-adequacy assessment, or dosing guide. Recorded stress and symptoms do not change this band.</p>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="Illustrative circadian context band exact values"><table><caption>Exact values supplied by {contextBand.band.revision}; missing samples remain missing.</caption><thead><tr><th scope="col">Local time</th><th scope="col">Lower context</th><th scope="col">Center context</th><th scope="col">Upper context</th></tr></thead><tbody>{contextBand.samples.map((sample) => <tr key={sample.occurred_at}><td>{experiencedTime(sample.occurred_at, data.exposure.timezone)}</td><td>{formatMeasurement(sample.lower_nmol_l, contextBand.series_unit)}</td><td>{formatMeasurement(sample.center_nmol_l, contextBand.series_unit)}</td><td>{formatMeasurement(sample.upper_nmol_l, contextBand.series_unit)}</td></tr>)}</tbody></table></div>
    </details>
    {data.temperature.length === 0 ? null : <div className="table-scroll" tabIndex={0} role="region" aria-label="Selected-day temperature exact values"><table className="vital-table"><caption>Exact selected-day body-temperature facts. Missing intervals remain blank.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Temperature</th></tr></thead><tbody>{data.temperature.map((record) => <tr key={record.id}><td>{experiencedTime(record.time.occurred_at, data.exposure.timezone)}</td><td>{record.display_f} °F ({record.display_c} °C)</td></tr>)}</tbody></table></div>}
    <details className="metric-definition model-methodology"><summary>How this model works: formulas, sources, and limits</summary>
      <p>{data.exposure.definition}</p>
      <h3>Exact implemented exposure formula</h3>
      <p><strong>{exposureModelVersion(data.exposure)}</strong> supports only {isWakeFreeCurve(data.exposure) ? supportedDoseDescription(data.exposure) : `${data.exposure.model.supported_formulation} ${data.exposure.model.supported_route} ${data.exposure.model.supported_medication}`} recorded in {data.exposure.model.amount_unit}.</p>
      {isWakeFreeCurve(data.exposure) ? <>
        <pre><code>{isMixedRouteCurve(data.exposure) ? `oral_free(t) = unchanged hc-wake-free-v3 oral contribution
iv_total(t) = 1,347 × (dose_mg / 50) × exp(-0.27 × elapsed_hours) nmol/L for each exact 50 mg or 100 mg IV push
combined_total(t) = total_from_free(oral_free(t)) + sum(iv_total(t))
combined_free(t) = free_from_total(combined_total(t))` : `ka = ${formatDecimal(data.exposure.model.parameters.absorption_rate_per_hour)} per hour
ke = ln(2) / ${formatDecimal(data.exposure.model.parameters.elimination_half_life_hours)} hours = ${formatDecimal(data.exposure.model.parameters.elimination_rate_per_hour)} per hour
t_peak = ln(ka / ke) / (ka - ke) = ${formatDecimal(data.exposure.model.parameters.peak_time_hours)} hours
free(t) = calibrated dose contribution × ka/(ka-ke) × (exp(-ke×t) - exp(-ka×t)) nmol/L
display total(t) = one-site CBG saturation + linear albumin binding applied to free(t)`}</code></pre>
        <p>{isMixedRouteCurve(data.exposure) ? "The full model leaves every oral v3 calculation unchanged, adds exact 50 mg and 100 mg intravenous-push facts in the total-cortisol domain, then performs one nonlinear binding conversion back to serum-free cortisol. The 50 mg contribution uses the published population fit; 100 mg is a disclosed 2× dose-proportional scenario rather than a separately fitted curve. Repeated injections add rather than reset one another. Other amounts and non-intravenous routes remain visible recorded markers but are not modeled." : "This wake-anchored model runs and sums every oral dose in serum free cortisol."} Its population defaults are user-editable in settings: half-life {formatDecimal(data.exposure.model.parameters.elimination_half_life_hours)} hours, peak time {formatDecimal(data.exposure.model.parameters.peak_time_hours)} hours, distribution volume {formatDecimal(data.exposure.model.parameters.distribution_volume_liters)} L, and oral bioavailability {formatDecimal(data.exposure.model.parameters.oral_bioavailability)}. Derived total cortisol is display-only. Samples occur every {data.exposure.model.sample_interval_minutes.toString()} elapsed minutes plus exact administration and modeled-peak knots.</p>
      </> : isPhysiologicalCurve(data.exposure) ? <>
        <pre><code>{`ka = ${formatDecimal(data.exposure.model.absorption_rate_per_hour)} per hour\nke = clearance / distribution volume = ${formatDecimal(data.exposure.model.elimination_rate_per_hour)} per hour\nt_peak = ln(ka / ke) / (ka - ke) = ${formatDecimal(data.exposure.model.peak_time_hours)} hours\nC(t) = (F × dose / V) × (1,000,000 / molecular weight) × ka/(ka-ke) × (exp(-ke×t) - exp(-ka×t)) nmol/L`}</code></pre>
        <p>This is a population-parameter plasma-free-cortisol scenario. It is not a measured value, personal target, medication-adequacy test, or dosing guide. Contributions from close or simultaneous doses are summed and sampled every {data.exposure.model.sample_interval_minutes.toString()} elapsed minutes plus exact administration and modeled-peak knots.</p>
      </> : <>
        <pre><code>{`ka = ${formatDecimal(data.exposure.model.absorption_rate_per_hour)} per hour\nke = ln(2) / ${formatDecimal(data.exposure.model.elimination_half_life_hours)} hours = ${formatDecimal(data.exposure.model.elimination_rate_per_hour)} per hour\nt_peak = ln(ka / ke) / (ka - ke) = ${formatDecimal(data.exposure.model.peak_time_hours)} hours\nraw(t) = exp(-ke × t) - exp(-ka × t)\nshape(t) = raw(t) / raw(t_peak)\ndose_contribution(t) = recorded_amount_mg × shape(t) REU\nstress_exposure(t) = sum of explicitly categorized stress-dose contributions\nregular_exposure(t) = sum of all other supported dose contributions\ntotal_exposure(t) = regular_exposure(t) + stress_exposure(t)`}</code></pre>
        <p>Each contribution is zero before its recorded administration and after {data.exposure.model.contribution_horizon_hours.toString()} hours. It rises from zero, reaches a normalized peak of 1 REU per recorded mg at <code>t_peak</code>, then declines. Contributions from close or simultaneous doses are summed; none resets another. Output is sampled every {data.exposure.model.sample_interval_minutes.toString()} elapsed minutes plus exact administration and modeled-peak knots. REU is a relative visualization unit, not nmol/L, µg/dL, biological effect, or medication adequacy.</p>
      </>}
      <h3>Why these parameters are used</h3>
      <ul>{data.exposure.model.references.map((href) => { const detail = EXPOSURE_REFERENCE_DETAILS[href]; return <li key={href}><a href={href} target="_blank" rel="noreferrer">{detail?.label ?? "Model source"}</a>{detail === undefined ? null : ` — ${detail.use}.`}</li>; })}</ul>
      <h3>Model comparison</h3>
      <div className="table-scroll" tabIndex={0} role="region" aria-label="HealthCurve exposure model comparison"><table><caption>The four selectable models remain separate and versioned.</caption><thead><tr><th scope="col">Model</th><th scope="col">Output</th><th scope="col">Unit</th><th scope="col">Interpretation boundary</th></tr></thead><tbody><tr><th scope="row">hc-exposure-v1</th><td>Normalized oral-dose exposure shape</td><td>REU</td><td>Relative visualization; not cortisol concentration</td></tr><tr><th scope="row">hc-physiology-v2</th><td>Population-parameter plasma-free-cortisol scenario</td><td>nmol/L</td><td>Modeled scenario; not measured or personalized cortisol</td></tr><tr><th scope="row">hc-wake-free-v3</th><td>Binding-aware oral serum-free-cortisol model with wake-anchored healthy reference</td><td>nmol/L free</td><td>Modeled and reference context; not measured cortisol, a personal target, or a dosing guide</td></tr><tr><th scope="row">hc-mixed-route-free-v4</th><td>Unchanged v3 oral model plus exact 50 mg and 100 mg IV-push facts</td><td>nmol/L free</td><td>50 mg fitted population reference and disclosed 2× 100 mg scenario; not measured cortisol, a personal target, or a dosing guide</td></tr></tbody></table></div>
      <>
        <h3>Illustrative circadian context band</h3>
        <pre><code>{`center(t) = shape-preserving PCHIP interpolation of versioned local-clock anchors\nlower(t) = 0.8 × center(t)\nupper(t) = 1.2 × center(t)`}</code></pre>
        <p>The band is an optional, default-hidden population-shape illustration in nmol/L. Its anchors come from the owner-supplied synthetic modeling scenario; published healthy-rhythm evidence informs only general shape and phase. It is not a demographic reference interval, normal range, personal target, or medication-adequacy range. Age, sex, height, and body weight do not create a clinically validated personal range, and recorded stress or symptoms do not modify it.</p>
      </>
      <h3>No “needed cortisol” formula is active</h3>
      <p>{isAbsoluteCortisolCurve(data.exposure) ? "HealthCurve calculates no Garmin-stress-derived or symptom-derived cortisol “needed” value." : "HealthCurve currently calculates no baseline, Garmin-stress-derived, or symptom-derived cortisol “needed” value. The supplied exploratory scenario used Req(t) = Base(t) × S(t), but its population baseline anchors and stress multipliers are not part of hc-exposure-v1."} Garmin stress remains a provider score on its own scale. Symptoms retain their recorded 0–10 severity and use <code>severity × 10</code> only for display position. Missing values remain missing. None of these inputs changes the exposure curve or becomes a dose multiplier, coverage ratio, or physiological requirement.</p>
      <p>That boundary exists because the available evidence describes hydrocortisone pharmacokinetics and stress physiology but does not validate a minute-by-minute conversion from Garmin stress or subjective symptoms to an individual cortisol requirement:</p>
      <ul>{REQUIREMENT_EVIDENCE.map((source) => <li key={source.href}><a href={source.href} target="_blank" rel="noreferrer">{source.label}</a>{` — ${source.use}.`}</li>)}</ul>
      <p><strong>Clinical boundary:</strong> Model output never overrides recorded symptoms, physician-approved plans, or physician-authored emergency instructions. Follow those instructions and seek appropriate clinical or emergency care regardless of how either model or the illustrative band appears.</p>
      <h3>Overlay display formula</h3>
      <p>{isWakeFreeCurve(data.exposure) ? "The selected binding-aware modeled curve and wake-anchored P5–P95 reference share one stable absolute serum-free-cortisol axis in nmol/L; neither is independently normalized, and hiding the reference does not rescale the modeled curve. " : ""}For every other numeric lane without a fixed domain, let <code>display_min</code> and <code>display_max</code> be its observed selected-day minimum and maximum. The graph uses <code>display = 100 × (value - display_min) / max(display_max - display_min, 1)</code>. An empty lane uses bounds 0 and 1. If every point equals <code>v</code>, the fallback bounds are <code>min(0, v)</code> and <code>v + max(1, abs(v) × 0.1)</code>. Garmin stress uses fixed bounds 0 and 100. Symptoms use fixed bounds 0 and 10, equivalent to <code>severity × 10</code>. Respiration uses a fixed 0–{RESPIRATION_DISPLAY_MAX.toString()} breaths/min display domain and a centered 5-sample median within each observed contiguous segment. Body temperature uses a fixed {TEMPERATURE_DISPLAY_MIN.toString()}–{TEMPERATURE_DISPLAY_MAX.toString()} °F structural display domain and discrete points; conversion uses <code>°F = (°C × 9/5) + 32</code>. Values outside fixed domains are clipped on the graph only. This changes only screen position; exact native values remain in the tooltip and authoritative Timeline. Relative heights are not equivalent measurements and do not establish cortisol need, correlation, or causation.</p>
    </details>
  </Paper>;
}
