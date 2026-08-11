import { render, screen, within } from "@testing-library/react";

import { AccessibleLineChart, type ChartSeries } from "./AccessibleLineChart";

const series: ChartSeries[] = [
  { name: "Recorded", source: "synthetic facts", values: [{ label: "2026-08-01", value: "1" }, { label: "2026-08-02", value: "2" }, { label: "2026-08-03", value: null }, { label: "2026-08-04", value: "3" }, { label: "2026-08-05", value: "4" }] },
  { name: "Plan", source: "synthetic approved plan", values: [{ label: "2026-08-01", value: "2" }, { label: "2026-08-02", value: "2" }, { label: "2026-08-03", value: "2" }, { label: "2026-08-04", value: "2" }, { label: "2026-08-05", value: "2" }] },
];

function chart(overrides: Partial<React.ComponentProps<typeof AccessibleLineChart>> = {}): React.JSX.Element {
  return <AccessibleLineChart title="Synthetic chart" summary="Synthetic values over time." unit="mg" timezone="Europe/London" dateRange="2026-08-01 through 2026-08-05" definition="Synthetic deterministic definition." sampleCount={7} missingCount={3} series={series} {...overrides} />;
}

describe("Accessible line chart", () => {
  it("labels both axes, shows readable ticks and tooltips, and preserves gaps and the exact table", () => {
    const { container } = render(chart({ xAxisLabel: "Date", yAxisLabel: "Dose total" }));

    expect(screen.getByRole("img", { name: /Synthetic chart/ })).toBeVisible();
    expect(screen.getByText("X axis: Date (Europe/London). Y axis: Dose total (mg).")).toBeVisible();
    expect(screen.getByRole("region", { name: "Synthetic chart scrollable graph" })).toBeVisible();
    expect([...container.querySelectorAll(".chart-x-tick text")].map((node) => node.textContent)).toEqual(["08-01", "08-02", "08-03", "08-04", "08-05"]);
    expect([...container.querySelectorAll(".chart-y-tick text")].map((node) => node.textContent)).toEqual(["1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0"]);
    expect(container.querySelectorAll('[data-series="Recorded"]')).toHaveLength(2);
    expect(container.querySelectorAll('[data-point-series="Recorded"]')).toHaveLength(4);
    expect(container.querySelector('[data-point-series="Recorded"] title')).toHaveTextContent("2026-08-01: 1 mg");
    expect(screen.getByText("Gap—no value")).toBeInTheDocument();
    expect(screen.getByText("Association does not establish causation.")).toBeVisible();
    expect(screen.getByText("Synthetic deterministic definition.")).toBeInTheDocument();
    expect(screen.getByText("View data table")).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Recorded (mg)" })).toBeInTheDocument();
    expect(screen.getByText(/source: synthetic facts/)).toBeVisible();
  });

  it("includes zero only when the metric requests a zero baseline", () => {
    const { container } = render(chart({ includeZero: true, series: [{ name: "Actual", source: "synthetic facts", values: [{ label: "2026-08-01", value: "20" }, { label: "2026-08-02", value: "25" }] }] }));
    expect([...container.querySelectorAll(".chart-y-tick text")].map((node) => node.textContent)).toContain("0");
  });

  it("renders a compact padded scale for one, two, and identical values", () => {
    const { container, rerender } = render(chart({
      compactPlot: true,
      yPadding: 1,
      series: [{ name: "Weight", source: "synthetic facts", values: [{ label: "2026-08-01", value: "180" }] }],
    }));

    expect(screen.getByRole("region", { name: "Synthetic chart scrollable graph" })).toHaveClass("chart-plot-scroll--compact");
    expect([...container.querySelectorAll(".chart-y-tick text")].map((node) => node.textContent)).toEqual(["179.0", "179.5", "180.0", "180.5", "181.0"]);
    expect(container.querySelectorAll('[data-point-series="Weight"]')).toHaveLength(1);

    rerender(chart({
      compactPlot: true,
      yPadding: 1,
      series: [{ name: "Weight", source: "synthetic facts", values: [{ label: "2026-08-01", value: "180" }, { label: "2026-08-02", value: "180" }] }],
    }));
    expect(container.querySelectorAll('[data-point-series="Weight"]')).toHaveLength(2);
    expect(container.querySelectorAll('[data-series="Weight"]')).toHaveLength(1);

    rerender(chart({
      compactPlot: true,
      yPadding: 1,
      series: [{ name: "Weight", source: "synthetic facts", values: [{ label: "2026-08-01", value: "180" }, { label: "2026-08-02", value: "181" }] }],
    }));
    expect([...container.querySelectorAll(".chart-y-tick text")].map((node) => node.textContent)).toEqual(["179.0", "179.5", "180.0", "180.5", "181.0", "181.5", "182.0"]);
  });

  it("does not plot values with incompatible units on one Y-axis", () => {
    render(chart({ unit: "mixed", series: [{ name: "Recorded", source: "synthetic facts", values: [{ label: "2026-08-01", value: "1", unit: "mg" }, { label: "2026-08-02", value: "2", unit: "mL" }] }] }));

    expect(screen.queryByRole("img", { name: /Synthetic chart/ })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("these values use different units");
    expect(screen.getByRole("columnheader", { name: "Recorded (unit shown per value)" })).toBeInTheDocument();
    const table = screen.getByRole("region", { name: "Synthetic chart data table" });
    expect(within(table).getByRole("cell", { name: "1 mg" })).toBeInTheDocument();
    expect(within(table).getByRole("cell", { name: "2 mL" })).toBeInTheDocument();
  });
});
