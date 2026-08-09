import { useId } from "react";

interface ChartValue {
  label: string;
  value: string | null;
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
  dateRange: string;
  definition: string;
  sampleCount: number;
  missingCount: number;
  series: ChartSeries[];
}

interface Point {
  x: number;
  y: number;
}

const WIDTH = 720;
const HEIGHT = 260;
const PADDING = 35;

function plottedSegments(values: ChartValue[], maximum: number): Point[][] {
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
    current.push({ x: PADDING + index / denominator * (WIDTH - PADDING * 2), y: HEIGHT - PADDING - numeric / maximum * (HEIGHT - PADDING * 2) });
  });
  if (current.length > 0) segments.push(current);
  return segments;
}

function path(points: Point[]): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
}

export function AccessibleLineChart({ title, summary, unit, timezone, dateRange, definition, sampleCount, missingCount, series }: AccessibleLineChartProps): React.JSX.Element {
  const headingId = useId();
  const maximum = Math.max(1, ...series.flatMap((item) => item.values.map((value) => value.value === null ? 0 : Number(value.value))).filter(Number.isFinite));
  const labels = series[0]?.values.map((value) => value.label) ?? [];
  return <section className="metric-card chart-card" aria-labelledby={headingId}><h2 id={headingId}>{title}</h2><p>{summary}</p><dl className="metric-metadata"><div><dt>Unit</dt><dd>{unit}</dd></div><div><dt>Timezone</dt><dd>{timezone}</dd></div><div><dt>Date range</dt><dd>{dateRange}</dd></div><div><dt>Sample count</dt><dd>{sampleCount}</dd></div><div><dt>Missing values</dt><dd>{missingCount}</dd></div></dl>
    {series.length > 1 ? <aside className="association-caution"><strong>Association does not establish causation.</strong> Overlaid series share a time axis for comparison only.</aside> : null}
    <div className="chart-legend" aria-label="Chart series">{series.map((item, index) => <span key={item.name}><i className={`series-key series-key--${(index % 3).toString()}`} aria-hidden="true" />{item.name} · source: {item.source}</span>)}</div>
    <svg className="line-chart" viewBox={`0 0 ${WIDTH.toString()} ${HEIGHT.toString()}`} role="img" aria-label={`${title}. ${summary}`}><title>{title}. {summary}</title><line x1={PADDING} y1={HEIGHT - PADDING} x2={WIDTH - PADDING} y2={HEIGHT - PADDING} className="chart-axis" /><line x1={PADDING} y1={PADDING} x2={PADDING} y2={HEIGHT - PADDING} className="chart-axis" />{series.map((item, seriesIndex) => plottedSegments(item.values, maximum).flatMap((segment, segmentIndex) => segment.length === 1 ? <circle key={`${item.name}-${segmentIndex.toString()}`} data-series={item.name} cx={segment[0]?.x} cy={segment[0]?.y} r="4" className={`chart-series chart-series--${(seriesIndex % 3).toString()}`} /> : <path key={`${item.name}-${segmentIndex.toString()}`} data-series={item.name} d={path(segment)} className={`chart-series chart-series--${(seriesIndex % 3).toString()}`} />))}</svg>
    <details className="chart-table"><summary>View data table</summary><div className="table-scroll" tabIndex={0} role="region" aria-label={`${title} data table`}><table><thead><tr><th scope="col">Date</th>{series.map((item) => <th scope="col" key={item.name}>{item.name} ({unit})</th>)}</tr></thead><tbody>{labels.map((label, index) => <tr key={label}><th scope="row">{label}</th>{series.map((item) => <td key={item.name}>{item.values[index]?.value ?? "Gap—no value"}</td>)}</tr>)}</tbody></table></div></details>
    <details className="metric-definition"><summary>Metric definition</summary><p>{definition}</p></details>
  </section>;
}
