import { fireEvent, render, screen, within } from "@testing-library/react";

import { AccessibleLineChart, type ChartSeries } from "./AccessibleLineChart";

const series: ChartSeries[] = [
  { name: "Recorded", source: "synthetic facts", values: [{ label: "2026-08-01", value: "1" }, { label: "2026-08-02", value: "2" }, { label: "2026-08-03", value: null }, { label: "2026-08-04", value: "3" }, { label: "2026-08-05", value: "4" }] },
  { name: "Plan", source: "synthetic approved plan", values: [{ label: "2026-08-01", value: "2" }, { label: "2026-08-02", value: "2" }, { label: "2026-08-03", value: "2" }, { label: "2026-08-04", value: "2" }, { label: "2026-08-05", value: "2" }] },
];

function chart(overrides: Partial<React.ComponentProps<typeof AccessibleLineChart>> = {}): React.JSX.Element {
  return <AccessibleLineChart title="Synthetic chart" summary="Synthetic values over time." unit="mg" timezone="Europe/London" timezoneReferenceDate="2026-08-05" dateRange="2026-08-01 through 2026-08-05" definition="Synthetic deterministic definition." sampleCount={7} missingCount={3} series={series} {...overrides} />;
}

describe("Accessible line chart", () => {
  it("labels both axes, shows readable ticks and tooltips, and preserves gaps and the exact table", () => {
    const { container } = render(chart({ xAxisLabel: "Date", yAxisLabel: "Dose total" }));

    expect(screen.getByRole("img", { name: /Synthetic chart/ })).toBeVisible();
    expect(screen.getByText("X axis: Date (GMT+1). Y axis: Dose total (mg).")).toBeVisible();
    expect(screen.getByRole("region", { name: "Synthetic chart interactive graph" })).toBeVisible();
    expect([...container.querySelectorAll(".chart-x-tick text")].map((node) => node.textContent)).toEqual(["08-01", "08-02", "08-03", "08-04", "08-05"]);
    expect([...container.querySelectorAll(".chart-y-tick text")].map((node) => node.textContent)).toEqual(["1", "1.5", "2", "2.5", "3", "3.5", "4"]);
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

  it("inspects all series at the nearest date with pointer and keyboard input", () => {
    const { container } = render(chart());
    const graph = screen.getByRole("region", { name: "Synthetic chart interactive graph" });
    const svg = screen.getByRole("img", { name: /Synthetic chart/ });
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 720, bottom: 320, width: 720, height: 320, toJSON: () => ({}) });

    fireEvent.mouseMove(svg, { clientX: 543 });
    expect(screen.getByRole("status")).toHaveTextContent("2026-08-04");
    expect(screen.getByRole("status")).toHaveTextContent("Recorded: 3 mg");
    expect(screen.getByRole("status")).toHaveTextContent("Plan: 2 mg");
    expect(container.querySelectorAll("circle.chart-series--active")).toHaveLength(2);

    fireEvent.focus(graph);
    fireEvent.keyDown(graph, { key: "Home" });
    expect(screen.getByRole("status")).toHaveTextContent("2026-08-01");
    fireEvent.keyDown(graph, { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("2026-08-02");
    fireEvent.keyDown(graph, { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("Recorded: Gap—no value");
    expect(screen.getByRole("status")).toHaveTextContent("Plan: 2 mg");
    fireEvent.keyDown(graph, { key: "End" });
    expect(screen.getByRole("status")).toHaveTextContent("2026-08-05");
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

    expect(screen.getByRole("region", { name: "Synthetic chart interactive graph" })).toHaveClass("chart-plot-scroll--compact");
    expect([...container.querySelectorAll(".chart-y-tick text")].map((node) => node.textContent)).toEqual(["179", "179.5", "180", "180.5", "181"]);
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
    expect([...container.querySelectorAll(".chart-y-tick text")].map((node) => node.textContent)).toEqual(["179", "179.5", "180", "180.5", "181", "181.5", "182"]);
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
