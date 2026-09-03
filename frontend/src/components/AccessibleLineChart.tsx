import { useId, useState } from "react";

import { formatDecimal, formatMeasurement, humanizeUnit } from "../format";
import { timezoneAbbreviation, timezoneAbbreviationForLocalDate } from "../time";

export interface ChartValue {
  label: string;
  value: string | null;
  unit?: string | null;
}

export interface ChartSeries {
  name: string;
  source: string;
  values: ChartValue[];
}

interface AccessibleLineChartProps {
  title: string;
  summary: string;
  unit: string;
  timezone: string;
  timezoneReferenceDate?: string | undefined;
  dateRange: string;
  definition: string;
  sampleCount: number;
  missingCount: number;
  series: ChartSeries[];
  xAxisLabel?: string;
  yAxisLabel?: string;
  includeZero?: boolean;
  yPadding?: number;
  compactPlot?: boolean;
}

interface Point {
  x: number;
  y: number;
  item: ChartValue;
}

interface Scale {
  minimum: number;
  maximum: number;
  step: number;
  ticks: number[];
}

const WIDTH = 720;
const HEIGHT = 320;
const LEFT = 82;
const RIGHT = 24;
const TOP = 20;
const BOTTOM = 70;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM;
const PLOT_BOTTOM = HEIGHT - BOTTOM;

function niceStep(range: number): number {
  if (!Number.isFinite(range) || range <= 0) return 1;
  const roughStep = range / 5;
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const fraction = roughStep / magnitude;
  const niceFraction = fraction <= 1.5 ? 1 : fraction <= 3 ? 2 : fraction <= 7 ? 5 : 10;
  return niceFraction * magnitude;
}

function chartScale(values: number[], includeZero = false, requestedPadding = 0): Scale {
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return { minimum: 0, maximum: 1, step: 0.2, ticks: [0, 0.2, 0.4, 0.6, 0.8, 1] };
  const padding = Number.isFinite(requestedPadding) && requestedPadding > 0 ? requestedPadding : 0;
  if (includeZero) {
    minimum = Math.min(0, minimum);
    maximum = Math.max(0, maximum);
  }
  if (minimum === maximum) {
    const equalValuePadding = padding || Math.abs(minimum) * 0.1 || 1;
    minimum = includeZero && minimum >= 0 ? 0 : minimum - equalValuePadding;
    maximum += equalValuePadding;
  } else if (padding > 0) {
    minimum -= padding;
    maximum += padding;
  }
  const step = niceStep(maximum - minimum);
  const scaleMinimum = includeZero && minimum >= 0 ? 0 : Math.floor(minimum / step) * step;
  const scaleMaximum = Math.ceil(maximum / step) * step;
  const ticks: number[] = [];
  const count = Math.round((scaleMaximum - scaleMinimum) / step);
  for (let index = 0; index <= count; index += 1) ticks.push(scaleMinimum + index * step);
  return { minimum: scaleMinimum, maximum: scaleMaximum, step, ticks };
}

function decimalPlaces(step: number): number {
  if (step >= 1) return 0;
  return Math.min(6, Math.max(0, Math.ceil(-Math.log10(step))));
}

function formatTick(value: number, step: number): string {
  const normalized = Math.abs(value) < step / 1_000_000 ? 0 : value;
  return formatDecimal(normalized.toFixed(decimalPlaces(step)));
}

function formatXAxisTick(label: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(label)) return label.slice(5);
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(label)) return `${label.slice(5, 10)} ${label.slice(11, 16)}`;
  return label.length > 18 ? `${label.slice(0, 17)}…` : label;
}

function xTickIndices(length: number): number[] {
  if (length <= 5) return Array.from({ length }, (_, index) => index);
  return [...new Set(Array.from({ length: 5 }, (_, index) => Math.round(index * (length - 1) / 4)))];
}

function plottedSegments(values: ChartValue[], scale: Scale): Point[][] {
  const segments: Point[][] = [];
  let current: Point[] = [];
  values.forEach((item, index) => {
    const numeric = item.value === null ? Number.NaN : Number(item.value);
    if (!Number.isFinite(numeric)) {
      if (current.length > 0) segments.push(current);
      current = [];
      return;
    }
    const denominator = Math.max(values.length - 1, 1);
    current.push({
      x: LEFT + index / denominator * PLOT_WIDTH,
      y: TOP + (scale.maximum - numeric) / (scale.maximum - scale.minimum) * PLOT_HEIGHT,
      item,
    });
  });
  if (current.length > 0) segments.push(current);
  return segments;
}

function path(points: Point[]): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
}

function valueUnits(series: ChartSeries[], fallbackUnit: string): Set<string> {
  return new Set(series.flatMap((item) => item.values.flatMap((value) => {
    if (value.value === null || !Number.isFinite(Number(value.value))) return [];
    return [value.unit ?? fallbackUnit];
  })));
}

function tableValue(value: ChartValue | undefined, mixedUnits: boolean, fallbackUnit: string): string {
  if (value?.value == null) return "Gap—no value";
  return mixedUnits ? formatMeasurement(value.value, value.unit ?? fallbackUnit) : formatDecimal(value.value);
}

export function AccessibleLineChart({
  title,
  summary,
  unit,
  timezone,
  timezoneReferenceDate,
  dateRange,
  definition,
  sampleCount,
  missingCount,
  series,
  xAxisLabel = "Experienced date / time",
  yAxisLabel = title,
  includeZero = false,
  yPadding = 0,
  compactPlot = false,
}: AccessibleLineChartProps): React.JSX.Element {
  const headingId = useId();
  const interactionInstructionsId = useId();
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const numericValues = series.flatMap((item) => item.values.flatMap((value) => value.value === null ? [] : [Number(value.value)])).filter(Number.isFinite);
  const units = valueUnits(series, unit);
  const mixedUnits = units.size > 1;
  const effectiveUnit = mixedUnits ? "Mixed units—see table" : [...units][0] ?? unit;
  const displayedUnit = mixedUnits ? effectiveUnit : humanizeUnit(effectiveUnit);
  const scale = chartScale(numericValues, includeZero, yPadding);
  const labels = series[0]?.values.map((value) => value.label) ?? [];
  const tickIndices = xTickIndices(labels.length);
  const timezoneLabel = timezoneReferenceDate === undefined
    ? timezoneAbbreviation(timezone)
    : timezoneAbbreviationForLocalDate(timezone, timezoneReferenceDate);
  const axisDescription = `X axis: ${xAxisLabel} (${timezoneLabel}). Y axis: ${yAxisLabel} (${displayedUnit}).`;
  const activeLabel = activeIndex === null ? null : labels[activeIndex] ?? null;

  function inspectPointer(event: React.MouseEvent<SVGSVGElement>): void {
    if (labels.length === 0) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (bounds.width <= 0) return;
    const viewBoxX = (event.clientX - bounds.left) / bounds.width * WIDTH;
    const ratio = Math.max(0, Math.min(1, (viewBoxX - LEFT) / PLOT_WIDTH));
    setActiveIndex(Math.round(ratio * Math.max(labels.length - 1, 0)));
  }

  function inspectKeyboard(event: React.KeyboardEvent<HTMLDivElement>): void {
    if (labels.length === 0) return;
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") setActiveIndex(0);
    else if (event.key === "End") setActiveIndex(labels.length - 1);
    else if (event.key === "ArrowLeft") setActiveIndex((current) => Math.max(0, (current ?? 0) - 1));
    else setActiveIndex((current) => Math.min(labels.length - 1, (current ?? -1) + 1));
  }

  return <section className="metric-card chart-card" aria-labelledby={headingId}>
    <h2 id={headingId}>{title}</h2>
    <p>{summary}</p>
    <dl className="metric-metadata"><div><dt>Unit</dt><dd>{displayedUnit}</dd></div><div><dt>Timezone</dt><dd>{timezoneLabel}</dd></div><div><dt>Date range</dt><dd>{dateRange}</dd></div><div><dt>Sample count</dt><dd>{formatDecimal(sampleCount)}</dd></div><div><dt>Missing values</dt><dd>{formatDecimal(missingCount)}</dd></div></dl>
    {series.length > 1 ? <aside className="association-caution"><strong>Association does not establish causation.</strong> Overlaid series share a time axis for comparison only.</aside> : null}
    <div className="chart-legend" aria-label="Chart series">{series.map((item, index) => <span key={item.name}><i className={`series-key series-key--${(index % 3).toString()}`} aria-hidden="true" />{item.name} · source: {item.source}</span>)}</div>
    <p className="chart-axis-description">{axisDescription}</p>
    {mixedUnits ? <p className="chart-unit-warning" role="status"><strong>Graph not plotted:</strong> these values use different units and cannot share one reliable Y-axis. Use the exact-value table below.</p> : <><p id={interactionInstructionsId} className="chart-interaction-instructions">Hover across the chart to inspect a date. For keyboard access, focus the chart and use the left and right arrow keys.</p><div className={`chart-plot-scroll${compactPlot ? " chart-plot-scroll--compact" : ""}`} tabIndex={0} role="region" aria-label={`${title} interactive graph`} aria-describedby={interactionInstructionsId} onFocus={() => { if (activeIndex === null && labels.length > 0) setActiveIndex(0); }} onBlur={() => { setActiveIndex(null); }} onKeyDown={inspectKeyboard}>
      <div className="chart-plot-canvas">
      <svg className="line-chart" viewBox={`0 0 ${WIDTH.toString()} ${HEIGHT.toString()}`} role="img" aria-label={`${title}. ${summary} ${axisDescription}`} onMouseMove={inspectPointer} onMouseLeave={() => { setActiveIndex(null); }}>
        <title>{title}. {summary} {axisDescription}</title>
        {scale.ticks.map((tick) => {
          const y = TOP + (scale.maximum - tick) / (scale.maximum - scale.minimum) * PLOT_HEIGHT;
          return <g key={tick} className="chart-y-tick"><line x1={LEFT} y1={y} x2={WIDTH - RIGHT} y2={y} className="chart-grid-line" /><text x={LEFT - 10} y={y} dy="0.35em" textAnchor="end">{formatTick(tick, scale.step)}</text></g>;
        })}
        {tickIndices.map((index) => {
          const x = LEFT + index / Math.max(labels.length - 1, 1) * PLOT_WIDTH;
          const anchor = index === 0 ? "start" : index === labels.length - 1 ? "end" : "middle";
          return <g key={`${labels[index] ?? ""}-${index.toString()}`} className="chart-x-tick"><line x1={x} y1={PLOT_BOTTOM} x2={x} y2={PLOT_BOTTOM + 6} className="chart-axis" /><text x={x} y={PLOT_BOTTOM + 23} textAnchor={anchor}>{formatXAxisTick(labels[index] ?? "")}</text></g>;
        })}
        <line x1={LEFT} y1={PLOT_BOTTOM} x2={WIDTH - RIGHT} y2={PLOT_BOTTOM} className="chart-axis" />
        <line x1={LEFT} y1={TOP} x2={LEFT} y2={PLOT_BOTTOM} className="chart-axis" />
        <text x={LEFT + PLOT_WIDTH / 2} y={HEIGHT - 9} textAnchor="middle" className="chart-axis-title">{xAxisLabel} ({timezoneLabel})</text>
        <text transform={`translate(17 ${String(TOP + PLOT_HEIGHT / 2)}) rotate(-90)`} textAnchor="middle" className="chart-axis-title">{yAxisLabel} ({displayedUnit})</text>
        {activeIndex === null ? null : <line className="chart-inspection-line" x1={LEFT + activeIndex / Math.max(labels.length - 1, 1) * PLOT_WIDTH} y1={TOP} x2={LEFT + activeIndex / Math.max(labels.length - 1, 1) * PLOT_WIDTH} y2={PLOT_BOTTOM} />}
        {series.map((item, seriesIndex) => {
          const segments = plottedSegments(item.values, scale);
          return <g key={item.name} aria-hidden="true">{segments.map((segment, segmentIndex) => segment.length > 1 ? <path key={`${item.name}-${segmentIndex.toString()}`} data-series={item.name} d={path(segment)} className={`chart-series chart-series--${(seriesIndex % 3).toString()}`} /> : null)}{segments.flat().map((point, pointIndex) => <circle key={`${item.name}-point-${pointIndex.toString()}`} data-point-series={item.name} cx={point.x} cy={point.y} r={activeLabel === point.item.label ? "6" : "4"} className={`chart-series chart-series--${(seriesIndex % 3).toString()}${activeLabel === point.item.label ? " chart-series--active" : ""}`}><title>{point.item.label}: {formatMeasurement(point.item.value, point.item.unit ?? effectiveUnit)}</title></circle>)}</g>;
        })}
      </svg>
      {activeIndex === null || activeLabel === null ? null : <output className="chart-inspection-tooltip" aria-live="polite" style={{ left: `${Math.max(18, Math.min(82, (LEFT + activeIndex / Math.max(labels.length - 1, 1) * PLOT_WIDTH) / WIDTH * 100)).toString()}%` }}><strong>{activeLabel}</strong>{series.map((item) => {
        const inspected = item.values[activeIndex];
        return <span key={item.name}>{item.name}: {inspected?.value == null ? "Gap—no value" : formatMeasurement(inspected.value, inspected.unit ?? effectiveUnit)}</span>;
      })}</output>}
      </div>
    </div></>}
    <details className="chart-table"><summary>View data table</summary><div className="table-scroll" tabIndex={0} role="region" aria-label={`${title} data table`}><table><thead><tr><th scope="col">Date / time</th>{series.map((item) => <th scope="col" key={item.name}>{item.name} ({mixedUnits ? "unit shown per value" : displayedUnit})</th>)}</tr></thead><tbody>{labels.map((label, index) => <tr key={label}><th scope="row">{label}</th>{series.map((item) => <td key={item.name}>{tableValue(item.values[index], mixedUnits, unit)}</td>)}</tr>)}</tbody></table></div></details>
    <details className="metric-definition"><summary>Metric definition</summary><p>{definition}</p></details>
  </section>;
}
