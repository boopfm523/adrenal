import { render, screen } from "@testing-library/react";

import { AccessibleLineChart } from "./AccessibleLineChart";

describe("Accessible line chart", () => {
  it("splits lines at gaps and always renders equivalent table and metadata", () => {
    const { container } = render(<AccessibleLineChart title="Synthetic chart" summary="Synthetic values over time." unit="mg" timezone="Europe/London" dateRange="2026-08-01 through 2026-08-05" definition="Synthetic deterministic definition." sampleCount={7} missingCount={3} series={[{ name: "Recorded", source: "synthetic facts", values: [{ label: "2026-08-01", value: "1" }, { label: "2026-08-02", value: "2" }, { label: "2026-08-03", value: null }, { label: "2026-08-04", value: "3" }, { label: "2026-08-05", value: "4" }] }, { name: "Plan", source: "synthetic approved plan", values: [{ label: "2026-08-01", value: "2" }, { label: "2026-08-02", value: "2" }, { label: "2026-08-03", value: "2" }, { label: "2026-08-04", value: "2" }, { label: "2026-08-05", value: "2" }] }]} />);

    expect(screen.getByRole("img", { name: /Synthetic chart/ })).toBeVisible();
    expect(container.querySelectorAll('[data-series="Recorded"]')).toHaveLength(2);
    expect(screen.getByText("Gap—no value")).toBeInTheDocument();
    expect(screen.getByText("Association does not establish causation.")).toBeVisible();
    expect(screen.getByText("Europe/London")).toBeVisible();
    expect(screen.getByText("Synthetic deterministic definition.")).toBeInTheDocument();
    expect(screen.getByText("View data table")).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Recorded (mg)" })).toBeInTheDocument();
    expect(screen.getByText(/source: synthetic facts/)).toBeVisible();
  });
});
