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
      model: { version: "hc-exposure-v1", supported_medication: "hydrocortisone", supported_formulation: "conventional immediate-release tablet", supported_route: "oral", amount_unit: "mg", absorption_rate_per_hour: "2", elimination_half_life_hours: "1.7", elimination_rate_per_hour: "0.407733", peak_time_hours: "0.998758", contribution_horizon_hours: 24, sample_interval_minutes: 5, references: ["https://doi.org/10.1002/j.1552-4604.1991.tb01906.x"] },
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
    expect(document.querySelectorAll("[data-series='heart_rate']")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Stress" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));
    expect(document.querySelectorAll("[data-series='heart_rate']")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Heart rate" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("checkbox", { name: "Heart rate" }));
    expect(document.querySelectorAll("[data-series='heart_rate']")).toHaveLength(0);
    fireEvent.click(screen.getByRole("checkbox", { name: "Heart rate" }));
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

    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));
    expect(document.querySelectorAll("path.healthcurve-series--heart_rate")).toHaveLength(2);
    expect(document.querySelectorAll("circle.healthcurve-point--heart_rate")).toHaveLength(0);
    expect(screen.getByLabelText("Visible series sample counts")).toHaveTextContent("Dense sample dots are hidden");
  });

  it("publishes the executable formula, evidence, and absence of a needed-value model", () => {
    render(<DailyHealthCurve data={data()} />);

    const methodology = screen.getByText("How this model works: formulas, sources, and limits").parentElement;
    expect(methodology).not.toBeNull();
    expect(methodology).toHaveTextContent("ke = ln(2) / 1.7 hours = 0.407733 per hour");
    expect(methodology).toHaveTextContent("t_peak = ln(ka / ke) / (ka - ke) = 0.998758 hours");
    expect(methodology).toHaveTextContent("total_exposure(t) = sum of every supported current dose contribution");
    expect(methodology).toHaveTextContent("no baseline, Garmin-stress-derived, or symptom-derived cortisol “needed” value");
    expect(methodology).toHaveTextContent("Req(t) = Base(t) × S(t)");
    expect(methodology).toHaveTextContent("display = 100 × (value - display_min) / max(display_max - display_min, 1)");
    expect(methodology).toHaveTextContent("fallback bounds are min(0, v) and v + max(1, abs(v) × 0.1)");
    expect(screen.getByRole("link", { name: "Derendorf et al. (1991)" })).toHaveAttribute("href", "https://doi.org/10.1002/j.1552-4604.1991.tb01906.x");
    expect(screen.getByRole("link", { name: "Boonen et al. (2013)" })).toHaveAttribute("href", "https://pubmed.ncbi.nlm.nih.gov/23506003/");
  });

  it("shows systolic and diastolic together while keeping pulse in heart rate", () => {
    render(<DailyHealthCurve data={data({ bloodPressure: [{
      id: "30000000-0000-4000-8000-000000000001",
      category: "fact",
      systolic_mmhg: 121,
      diastolic_mmhg: 81,
      pulse_bpm: 88,
      time: { occurred_at: "2026-03-08T16:00:00Z", local_time: "2026-03-08T12:00:00", timezone: "America/New_York", utc_offset_minutes: -240 },
      provenance: { ...provenance, source_type: "telegram", confirmation_state: "confirmed_from_draft" },
      notes: null,
    }] })} />);

    fireEvent.click(screen.getByRole("button", { name: "Blood pressure" }));
    expect(document.querySelectorAll("circle.healthcurve-point--blood_pressure")).toHaveLength(2);
    fireEvent.change(screen.getByRole("slider", { name: "Explore daily HealthCurve by time" }), { target: { value: "660" } });
    const readout = screen.getByRole("status");
    expect(readout).toHaveTextContent("Systolic: 121 mmHg");
    expect(readout).toHaveTextContent("Diastolic: 81 mmHg");
    expect(readout).not.toHaveTextContent("88 bpm");
    const table = screen.getByRole("region", { name: "Daily HealthCurve exact values" });
    expect(table).toHaveTextContent("Systolic: 121 mmHg");
    expect(table).toHaveTextContent("Diastolic: 81 mmHg");

    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));
    expect(screen.getByRole("status")).toHaveTextContent("Blood-pressure pulse: 88 bpm");
  });

  it("shows a daily Garmin summary as untimed context without inventing a chart point", () => {
    const dailyStress: GarminRecord = {
      ...sample(0),
      id: "20000000-0000-4000-8000-000000000001",
      kind: "daily" as const,
      summary: "Stress: 31",
      metric_type: "stress",
      value: "31",
      unit: "garmin_score",
      aggregation: "daily_summary",
      sample_interval_seconds: null,
      garmin_field_name: "averageStressLevel",
      measurement_label: "Stress",
      period_label: "daily average",
    };
    const nightlyHrv: GarminRecord = {
      ...dailyStress,
      id: "20000000-0000-4000-8000-000000000002",
      summary: "Nightly average HRV: 41 ms",
      metric_type: "hrv",
      value: "41",
      unit: "ms",
      garmin_field_name: "lastNightAvg",
      measurement_label: "Nightly average HRV",
      period_label: "previous night",
    };
    const wakingRespiration: GarminRecord = {
      ...dailyStress,
      id: "20000000-0000-4000-8000-000000000003",
      summary: "Average waking respiration: 14.2 breaths/min",
      metric_type: "respiration_rate",
      value: "14.2",
      unit: "breaths/min",
      garmin_field_name: "avgWakingRespirationValue",
      measurement_label: "Average waking respiration",
      period_label: "waking period",
    };
    render(<DailyHealthCurve data={data({ garmin: [dailyStress, nightlyHrv, wakingRespiration] })} />);

    expect(document.querySelectorAll("circle.healthcurve-point--stress")).toHaveLength(0);
    expect(document.querySelectorAll("path.healthcurve-series--stress")).toHaveLength(0);
    expect(document.querySelectorAll("circle.healthcurve-point--hrv")).toHaveLength(0);
    expect(document.querySelectorAll("circle.healthcurve-point--respiration_rate")).toHaveLength(0);
    const context = screen.getByRole("region", { name: "Garmin aggregate context" });
    expect(context).toHaveTextContent("Stress score: 31");
    expect(context).toHaveTextContent("Untimed · daily average");
    expect(context).not.toHaveTextContent("Nightly average HRV");
    fireEvent.click(screen.getByRole("button", { name: "All series (busy)" }));
    expect(context).toHaveTextContent("Nightly average HRV41 msUntimed · previous night");
    expect(context).toHaveTextContent("Average waking respiration14.2 breaths/minUntimed · waking period");
    expect(context).toHaveTextContent("no exact intraday observation time");
    expect(screen.getByRole("status")).not.toHaveTextContent("Garmin stress: 31 score");
    const table = screen.getByRole("region", { name: "Daily HealthCurve exact values" });
    expect(table).toHaveTextContent("2026-03-08 · untimed aggregate");
    expect(table).toHaveTextContent("Garmin provider imported; untimed daily summary");
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

    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));
    expect(document.querySelectorAll("circle.healthcurve-point--heart_rate")).toHaveLength(0);
    expect(document.querySelectorAll("path.healthcurve-series--heart_rate").length).toBeGreaterThan(0);
    const table = screen.getByRole("region", { name: "Daily HealthCurve exact values" });
    expect(within(table).getAllByText("Heart rate")).toHaveLength(1_000);
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

    fireEvent.click(screen.getByRole("button", { name: "Recorded events" }));
    expect(document.querySelectorAll("circle.healthcurve-point--symptoms")).toHaveLength(0);
    const table = screen.getByRole("region", { name: "Daily HealthCurve exact values" });
    expect(within(table).getByText("Synthetic fatigue: severity missing")).toBeInTheDocument();
  });
});
