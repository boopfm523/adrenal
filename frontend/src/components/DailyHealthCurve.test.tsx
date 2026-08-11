import { fireEvent, render, screen, within } from "@testing-library/react";
import { vi } from "vitest";

import type { GarminRecord } from "../api/client";
import { DailyHealthCurve, type DailyHealthCurveData } from "./DailyHealthCurve";

const provenance = { recorded_at: "2026-03-08T05:00:00Z", source_type: "provider" as const, confirmation_state: "provider_imported" as const, supersedes_id: null, correction_reason: null, is_correction: false };

function data(overrides: Partial<DailyHealthCurveData> = {}): DailyHealthCurveData {
  return {
    exposure: {
      date: "2026-03-08",
      timezone: "America/New_York",
      day_start: "2026-03-08T05:00:00Z",
      day_end: "2026-03-09T04:00:00Z",
      elapsed_hours: "23",
      series_name: "Theoretical hydrocortisone exposure",
      series_unit: "REU",
      safety_label: "Theoretical hydrocortisone exposure—not a cortisol measurement or dosing guide.",
      definition: "Synthetic deterministic exposure definition.",
      model: { version: "hc-exposure-v1", supported_medication: "hydrocortisone", supported_formulation: "conventional immediate-release tablet", supported_route: "oral", amount_unit: "mg", absorption_rate_per_hour: "2", elimination_half_life_hours: "1.7", elimination_rate_per_hour: "0.4", peak_time_hours: "1", contribution_horizon_hours: 24, sample_interval_minutes: 5, references: [] },
      dose_markers: [],
      samples: [
        { occurred_at: "2026-03-08T05:00:00Z", local_time: "2026-03-08T00:00:00", utc_offset_minutes: -300, theoretical_exposure_reu: "0" },
        { occurred_at: "2026-03-09T04:00:00Z", local_time: "2026-03-09T00:00:00", utc_offset_minutes: -240, theoretical_exposure_reu: "0" },
      ],
      supported_dose_count: 0,
      excluded_dose_count: 0,
    },
    garmin: [],
    symptoms: [],
    bloodPressure: [],
    episodes: [],
    ...overrides,
  };
}

function sample(index: number): GarminRecord {
  const occurredAt = new Date(Date.parse("2026-03-08T05:00:00Z") + index * 60_000).toISOString();
  return {
    id: `00000000-0000-4000-8000-${index.toString().padStart(12, "0")}`,
    kind: "sample",
    summary: `Heart rate: ${(60 + index % 40).toString()} bpm`,
    time: { occurred_at: occurredAt, local_time: occurredAt.slice(0, 19), timezone: "America/New_York", utc_offset_minutes: -300 },
    provenance,
    metric_type: "heart_rate",
    value: (60 + index % 40).toString(),
    unit: "bpm",
    aggregation: "provider_sample",
    sample_interval_seconds: 60,
  };
}

describe("Daily HealthCurve", () => {
  it("uses one interactive overlay and exposes exact native values at the explored time", () => {
    render(<DailyHealthCurve data={data({ garmin: [sample(60)] })} />);

    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByRole("img")).toHaveAccessibleName(/interactive selected-day HealthCurve overlay/i);
    expect(document.querySelectorAll("[data-series='exposure']")).toHaveLength(1);
    expect(document.querySelectorAll("[data-series='heart_rate']")).toHaveLength(1);

    fireEvent.change(screen.getByRole("slider", { name: "Explore daily HealthCurve by time" }), { target: { value: "60" } });
    const readout = screen.getByRole("status");
    expect(within(readout).getByText("Heart rate:")).toBeVisible();
    expect(readout).toHaveTextContent("Heart rate: 80 bpm");
    expect(readout).toHaveTextContent(/GMT-5/);

    const pointerTarget = document.querySelector<SVGRectElement>(".healthcurve-pointer-target");
    if (pointerTarget === null) throw new Error("pointer target missing");
    vi.spyOn(pointerTarget, "getBoundingClientRect").mockReturnValue({ left: 0, width: 100 } as DOMRect);
    fireEvent.pointerMove(pointerTarget, { clientX: 50 });
    expect(screen.getByRole("slider", { name: "Explore daily HealthCurve by time" })).toHaveValue("690");
  });

  it("breaks recorded curves across missing cadence intervals", () => {
    render(<DailyHealthCurve data={data({ garmin: [sample(0), sample(1), sample(10), sample(11)] })} />);

    expect(document.querySelectorAll("path.healthcurve-series--heart_rate")).toHaveLength(2);
    expect(document.querySelectorAll("circle.healthcurve-point--heart_rate")).toHaveLength(4);
  });

  it("renders empty context lanes and the true 23-hour DST axis without inventing data", () => {
    render(<DailyHealthCurve data={data()} />);

    expect(screen.getByText("23 hours")).toBeVisible();
    expect(screen.getAllByText(/GMT-5/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/GMT-4/).length).toBeGreaterThan(0);
    const summaries = screen.getByLabelText("Visible series sample counts");
    expect(within(summaries).getByText(/Garmin stress:/).parentElement).toHaveTextContent("0 exact point");
    expect(screen.getByText(/expected missing counts are not invented/)).toBeVisible();
  });

  it("keeps a large sampled day available in the chart and exact-value table", () => {
    const garmin = Array.from({ length: 1_000 }, (_, index) => sample(index));
    render(<DailyHealthCurve data={data({ garmin })} />);

    expect(document.querySelectorAll("circle.healthcurve-point--heart_rate")).toHaveLength(1_000);
    const table = screen.getByRole("region", { name: "Daily HealthCurve exact values" });
    expect(within(table).getAllByText(/Heart rate:/)).toHaveLength(1_000);
  });

  it("keeps a symptom with missing severity out of the numeric chart without hiding the fact", () => {
    render(<DailyHealthCurve data={data({ symptoms: [{
      id: "10000000-0000-4000-8000-000000000001",
      category: "fact",
      name: "Synthetic fatigue",
      severity: null,
      body_area: null,
      time: { occurred_at: "2026-03-08T16:00:00Z", local_time: "2026-03-08T12:00:00", timezone: "America/New_York", utc_offset_minutes: -240 },
      provenance: { ...provenance, source_type: "web", confirmation_state: "direct" },
      episode_id: null,
      notes: null,
    }] })} />);

    expect(document.querySelectorAll("circle.healthcurve-point--symptoms")).toHaveLength(0);
    const table = screen.getByRole("region", { name: "Daily HealthCurve exact values" });
    expect(within(table).getByText("Synthetic fatigue: severity missing")).toBeInTheDocument();
  });
});
