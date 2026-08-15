import { fireEvent, render, screen, within, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";

import type { GarminRecord } from "../api/client";
import { HealthCurveProvider } from "./HealthCurveProvider";
import { DailyHealthCurve, type DailyHealthCurveData } from "./DailyHealthCurve";

function renderWithTheme(ui: ReactNode): RenderResult {
  return render(ui, { wrapper: HealthCurveProvider });
}

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
        { occurred_at: "2026-03-08T05:00:00Z", local_time: "2026-03-08T00:00:00", utc_offset_minutes: -300, theoretical_exposure_reu: "0", regular_exposure_reu: "0", stress_exposure_reu: "0" },
        { occurred_at: "2026-03-08T06:00:00Z", local_time: "2026-03-08T01:00:00", utc_offset_minutes: -300, theoretical_exposure_reu: "5.509920038", regular_exposure_reu: "3.009920038", stress_exposure_reu: "2.5" },
        { occurred_at: "2026-03-09T04:00:00Z", local_time: "2026-03-09T00:00:00", utc_offset_minutes: -240, theoretical_exposure_reu: "0", regular_exposure_reu: "0", stress_exposure_reu: "0" },
      ],
      supported_dose_count: 0,
      excluded_dose_count: 0,
    },
    garmin: [],
    symptoms: [],
    bloodPressure: [],
    temperature: [],
    episodes: [],
    ...overrides,
  };
}

function physiologicalData(): DailyHealthCurveData {
  return data({
    exposure: {
      date: "2026-03-08",
      timezone: "America/New_York",
      day_start: "2026-03-08T05:00:00Z",
      day_end: "2026-03-09T04:00:00Z",
      elapsed_hours: "23",
      series_kind: "modeled_plasma_free_cortisol_scenario",
      series_name: "Modeled plasma-free-cortisol scenario",
      series_unit: "nmol/L",
      safety_label: "Population-parameter modeled scenario—not a measurement or dosing guide.",
      definition: "Synthetic physiological scenario.",
      model: { id: "hc-physiology-v2", revision: "hc-physiology-v2.0.0", supported_medication: "hydrocortisone", supported_formulation: "conventional immediate-release tablet", supported_route: "oral", amount_unit: "mg", absorption_rate_per_hour: "1.4", oral_bioavailability: "0.96", clearance_liters_per_hour: "235.78", distribution_volume_liters: "474.38", cortisol_molecular_weight: "362.46", elimination_half_life_hours: "1.39", elimination_rate_per_hour: "0.497", peak_time_hours: "1.147", contribution_horizon_hours: 48, sample_interval_minutes: 5, references: [] },
      source_revision_sha256: "a".repeat(64),
      dose_markers: [],
      samples: [
        { occurred_at: "2026-03-08T05:00:00Z", local_time: "2026-03-08T00:00:00", utc_offset_minutes: -300, modeled_free_cortisol_nmol_l: "0", regular_modeled_free_cortisol_nmol_l: "0", stress_modeled_free_cortisol_nmol_l: "0" },
        { occurred_at: "2026-03-08T06:00:00Z", local_time: "2026-03-08T01:00:00", utc_offset_minutes: -300, modeled_free_cortisol_nmol_l: "40", regular_modeled_free_cortisol_nmol_l: "40", stress_modeled_free_cortisol_nmol_l: "0" },
        { occurred_at: "2026-03-09T04:00:00Z", local_time: "2026-03-09T00:00:00", utc_offset_minutes: -240, modeled_free_cortisol_nmol_l: "1", regular_modeled_free_cortisol_nmol_l: "1", stress_modeled_free_cortisol_nmol_l: "0" },
      ],
      supported_dose_count: 0,
      excluded_dose_count: 0,
      context_band: {
        date: "2026-03-08", timezone: "America/New_York", day_start: "2026-03-08T05:00:00Z", day_end: "2026-03-09T04:00:00Z", elapsed_hours: "23", series_kind: "illustrative_circadian_context_band", series_name: "Illustrative circadian context band", series_unit: "nmol/L", default_visible: false, safety_label: "Illustrative population-shape context only—not a personal target or adequacy range.",
        band: { id: "hc-circadian-context-v1", revision: "hc-circadian-context-v1.0.0", interpolation: "pchip-no-overshoot", lower_multiplier: "0.8", upper_multiplier: "1.2", anchor_origin: "owner_supplied_synthetic_scenario", healthy_rhythm_evidence_scope: "shape_and_phase_context_only", personalized: false, body_context_used: false, demographic_reference_interval: false, references: [], anchors: [] },
        recorded_stress_context: { episode_count: 1, missing_severity_count: 0, multiplier: "1", applied_to_band: false, applied_to_drug_model: false, reason: "No validated mapping." },
        samples: [
          { occurred_at: "2026-03-08T05:00:00Z", local_time: "2026-03-08T00:00:00", utc_offset_minutes: -300, center_nmol_l: "20", lower_nmol_l: "16", upper_nmol_l: "24" },
          { occurred_at: "2026-03-08T06:00:00Z", local_time: "2026-03-08T01:00:00", utc_offset_minutes: -300, center_nmol_l: "25", lower_nmol_l: "20", upper_nmol_l: "30" },
          { occurred_at: "2026-03-09T04:00:00Z", local_time: "2026-03-09T00:00:00", utc_offset_minutes: -240, center_nmol_l: "18", lower_nmol_l: "14.4", upper_nmol_l: "21.6" },
        ],
      },
    },
  });
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

function hoverAt(minute: number): { target: SVGRectElement; tooltip: HTMLElement } {
  const target = document.querySelector<SVGRectElement>(".healthcurve-pointer-target");
  if (target === null) throw new Error("pointer target missing");
  vi.spyOn(target, "getBoundingClientRect").mockReturnValue({ left: 0, width: 1_380 } as DOMRect);
  fireEvent.pointerEnter(target);
  fireEvent.pointerMove(target, { clientX: minute });
  return { target, tooltip: screen.getByRole("tooltip") };
}

describe("Daily HealthCurve", () => {
  it("keeps guidance, missingness, causation, and the model in one collapsed disclosure", () => {
    renderWithTheme(<DailyHealthCurve data={data()} />);

    const summary = screen.getByText("HealthCurve context and limits");
    const context = summary.parentElement;
    if (context === null) throw new Error("HealthCurve context disclosure missing");
    expect(context).not.toHaveAttribute("open");
    expect(within(context).getByText("Exposure model").parentElement).toHaveTextContent("hc-exposure-v1");
    expect(context).toHaveTextContent("Association does not establish causation");
    expect(context).toHaveTextContent("Focused comparison on one time axis");
    expect(context).toHaveTextContent("Missingness: Garmin cadence is observational");
    expect(screen.getByText("Selected date")).toBeVisible();
    fireEvent.click(summary);
    expect(context).toHaveAttribute("open");
  });

  it("uses one interactive overlay and exposes exact native values at the explored time", () => {
    renderWithTheme(<DailyHealthCurve data={data({ garmin: [sample(60)] })} />);

    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByRole("img")).toHaveAccessibleName(/interactive selected-day HealthCurve overlay/i);
    const controls = screen.getByLabelText("HealthCurve chart controls");
    const legend = screen.getByLabelText("Overlay series legend");
    const mobileControls = screen.getByRole("group", { name: "Mobile chart controls" });
    const chart = screen.getByRole("region", { name: "Daily HealthCurve synchronized chart" });
    expect(controls.nextElementSibling).toBe(legend);
    expect(legend.nextElementSibling).toBe(mobileControls);
    expect(mobileControls.nextElementSibling).toBe(chart);
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

    expect(screen.queryByRole("slider", { name: "Explore daily HealthCurve by time" })).not.toBeInTheDocument();
    const { target: pointerTarget, tooltip } = hoverAt(60);
    expect(tooltip).toHaveTextContent("Heart rate: 80 bpm");
    expect(tooltip).toHaveTextContent("Theoretical exposure: 5.510 REU");
    expect(tooltip).toHaveTextContent("Regular-dose contribution: 3.010 REU");
    expect(tooltip).toHaveTextContent("Stress-dose contribution: 2.500 REU");
    expect(tooltip.textContent.match(/GMT-5/g)).toHaveLength(1);
    expect(tooltip).not.toHaveTextContent(/at 2026-/);
    expect(tooltip).not.toHaveTextContent(/hc-exposure-v1|provider_imported|observed cadence/i);
    expect(screen.queryByText("View exact values and provenance")).not.toBeInTheDocument();
    expect(screen.getByRole("img").querySelector(":scope > title")).toBeNull();
    const tickLabels = [...document.querySelectorAll(".healthcurve-time-label")].map((element) => element.textContent);
    expect(tickLabels).toEqual(["00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "00:00"]);
    expect(tickLabels.every((label) => /^\d{2}:\d{2}$/.test(label))).toBe(true);
    expect(tickLabels.join(" ")).not.toMatch(/AM|PM|GMT/);
    expect(document.querySelectorAll(".healthcurve-hour-mark")).toHaveLength(24);
    expect(document.querySelectorAll(".healthcurve-hour-tick")).toHaveLength(17);
    expect(document.querySelectorAll(".healthcurve-hour-tick .healthcurve-time-label")).toHaveLength(0);
    fireEvent.pointerMove(pointerTarget, { clientX: 690 });
    expect(tooltip).toHaveTextContent("No exact observation at this time");
    fireEvent.pointerLeave(pointerTarget);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("renders the v2 context band as an independent default-hidden accessible ribbon", () => {
    const { rerender } = renderWithTheme(<DailyHealthCurve data={physiologicalData()} />);

    const toggle = screen.getByRole("checkbox", { name: /Illustrative circadian context band/ });
    expect(toggle).not.toBeChecked();
    expect(document.querySelector("[data-series='context-band']")).toBeNull();
    expect(screen.getByLabelText("Overlay series legend")).not.toHaveTextContent("Illustrative circadian context");

    fireEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(document.querySelector("[data-series='context-band'] .healthcurve-context-band")).toHaveAttribute("d", expect.stringContaining("Z"));
    expect(screen.getByLabelText("Overlay series legend")).toHaveTextContent("Illustrative circadian context · nmol/L");
    expect(hoverAt(60).tooltip).toHaveTextContent("Illustrative circadian context: 20.0–30.0 nmol/L (center 25.0)");

    const exactValues = screen.getByText("Illustrative circadian context band values").parentElement;
    if (exactValues === null) throw new Error("context-band exact-value disclosure missing");
    expect(exactValues).not.toHaveAttribute("open");
    expect(exactValues).toHaveTextContent("not a personal target");
    expect(within(exactValues).getByRole("region", { name: "Illustrative circadian context band exact values" })).toHaveTextContent("20 nmol/L");

    fireEvent.click(toggle);
    expect(document.querySelector("[data-series='context-band']")).toBeNull();
    rerender(<DailyHealthCurve data={data()} />);
    expect(screen.queryByRole("checkbox", { name: /Illustrative circadian context band/ })).not.toBeInTheDocument();
  });

  it("shows touch-selected values in the chart tooltip and stable phone readout", () => {
    renderWithTheme(<DailyHealthCurve data={data({ garmin: [sample(60), sample(120)] })} />);
    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));

    const target = document.querySelector<SVGRectElement>(".healthcurve-pointer-target");
    if (target === null) throw new Error("pointer target missing");
    vi.spyOn(target, "getBoundingClientRect").mockReturnValue({ left: 0, width: 1_380 } as DOMRect);
    const setPointerCapture = vi.fn();
    const releasePointerCapture = vi.fn();
    target.setPointerCapture = setPointerCapture;
    target.hasPointerCapture = vi.fn(() => true);
    target.releasePointerCapture = releasePointerCapture;

    const readout = screen.getByRole("region", { name: "Selected chart time and values" });
    expect(readout).toHaveTextContent("tap a time, then drag left or right");
    fireEvent.pointerDown(target, { pointerId: 7, pointerType: "touch", clientX: 60 });
    expect(setPointerCapture).toHaveBeenCalledWith(7);
    expect(readout).toHaveTextContent("Heart rate: 80 bpm");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Heart rate: 80 bpm");
    expect(screen.getByRole("tooltip")).toHaveTextContent("2026-03-08, 01:00 GMT-5");

    fireEvent.click(within(readout).getByRole("button", { name: "Next observation →" }));
    expect(readout).toHaveTextContent("Heart rate: 60 bpm");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Heart rate: 60 bpm");
    fireEvent.pointerUp(target, { pointerId: 7, pointerType: "touch" });
    expect(releasePointerCapture).toHaveBeenCalledWith(7);
  });

  it("offers large mobile zoom controls without changing desktop hover behavior", () => {
    renderWithTheme(<DailyHealthCurve data={data()} />);

    const chart = screen.getByRole("img");
    expect(chart).toHaveClass("healthcurve-chart--zoom-1");
    fireEvent.click(screen.getByRole("button", { name: "Zoom chart in" }));
    expect(chart).toHaveClass("healthcurve-chart--zoom-1-5");
    expect(screen.getByText("1.5×")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zoom chart in" }));
    expect(chart).toHaveClass("healthcurve-chart--zoom-2");
    expect(screen.getByRole("button", { name: "Zoom chart in" })).toBeDisabled();

    expect(hoverAt(60).tooltip).toHaveTextContent("Theoretical exposure: 5.510 REU");
  });

  it("breaks recorded curves across missing cadence intervals", () => {
    renderWithTheme(<DailyHealthCurve data={data({ garmin: [sample(0), sample(1), sample(10), sample(11)] })} />);

    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));
    expect(document.querySelectorAll("path.healthcurve-series--heart_rate")).toHaveLength(2);
    expect(document.querySelectorAll("circle.healthcurve-point--heart_rate")).toHaveLength(0);
    fireEvent.focus(screen.getByRole("button", { name: "About Heart rate data" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("4 exact point(s)");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Dense sample dots are hidden");
  });

  it("calms the respiration line without changing exact samples or bridging gaps", () => {
    const respiration = [15, 15, 30, 15, 15, 20, 20].map((value, position) => {
      const index = position < 5 ? position : position + 5;
      return {
        ...sample(index),
        summary: `Respiration: ${value.toString()} breaths/min`,
        metric_type: "respiration_rate",
        value: value.toString(),
        unit: "breaths/min",
      } satisfies GarminRecord;
    });
    renderWithTheme(<DailyHealthCurve data={data({ garmin: respiration })} />);

    fireEvent.click(screen.getByRole("button", { name: "Respiration" }));
    const paths = document.querySelectorAll("path.healthcurve-series--respiration_rate");
    expect(paths).toHaveLength(2);
    expect(paths[0]).toHaveAttribute("d", expect.stringContaining("236.25"));
    expect(paths[0]).not.toHaveAttribute("d", expect.stringContaining("112.50"));
    expect(screen.getByLabelText("Overlay series legend")).toHaveTextContent("calmer 5-sample median line");
    expect(screen.getByLabelText("Series sample counts")).toHaveTextContent("Observed average: 18.6 breaths/min");
    fireEvent.focus(screen.getByRole("button", { name: "About Respiration data" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("fixed 0–40 breaths/min range");
    fireEvent.blur(screen.getByRole("button", { name: "About Respiration data" }));

    const { tooltip } = hoverAt(2);
    expect(tooltip).toHaveTextContent("Respiration: 30 breaths/min");
  });

  it("publishes the executable formula, evidence, and absence of a needed-value model", () => {
    renderWithTheme(<DailyHealthCurve data={data()} />);

    const methodology = screen.getByText("How this model works: formulas, sources, and limits").parentElement;
    expect(methodology).not.toBeNull();
    expect(methodology).toHaveTextContent("ke = ln(2) / 1.7 hours = 0.407733 per hour");
    expect(methodology).toHaveTextContent("t_peak = ln(ka / ke) / (ka - ke) = 0.998758 hours");
    expect(methodology).toHaveTextContent("stress_exposure(t) = sum of explicitly categorized stress-dose contributions");
    expect(methodology).toHaveTextContent("total_exposure(t) = regular_exposure(t) + stress_exposure(t)");
    expect(methodology).toHaveTextContent("no baseline, Garmin-stress-derived, or symptom-derived cortisol “needed” value");
    expect(methodology).toHaveTextContent("Req(t) = Base(t) × S(t)");
    expect(methodology).toHaveTextContent("display = 100 × (value - display_min) / max(display_max - display_min, 1)");
    expect(methodology).toHaveTextContent("fallback bounds are min(0, v) and v + max(1, abs(v) × 0.1)");
    expect(screen.getByRole("link", { name: "Derendorf et al. (1991)" })).toHaveAttribute("href", "https://doi.org/10.1002/j.1552-4604.1991.tb01906.x");
    expect(screen.getByRole("link", { name: "Boonen et al. (2013)" })).toHaveAttribute("href", "https://pubmed.ncbi.nlm.nih.gov/23506003/");
  });

  it("shows systolic and diastolic together while keeping pulse in heart rate", () => {
    renderWithTheme(<DailyHealthCurve data={data({ bloodPressure: [{
      id: "30000000-0000-4000-8000-000000000001",
      category: "fact",
      systolic_mmhg: 121,
      diastolic_mmhg: 81,
      pulse_bpm: 88,
      measurement_setting: "home",
      time: { occurred_at: "2026-03-08T16:00:00Z", local_time: "2026-03-08T12:00:00", timezone: "America/New_York", utc_offset_minutes: -240 },
      provenance: { ...provenance, source_type: "telegram", confirmation_state: "confirmed_from_draft" },
      notes: null,
    }, {
      id: "30000000-0000-4000-8000-000000000002",
      category: "fact",
      systolic_mmhg: 130,
      diastolic_mmhg: 85,
      pulse_bpm: null,
      measurement_setting: "provider",
      time: { occurred_at: "2026-03-08T18:00:00Z", local_time: "2026-03-08T14:00:00", timezone: "America/New_York", utc_offset_minutes: -240 },
      provenance: { ...provenance, source_type: "web", confirmation_state: "direct" },
      notes: null,
    }] })} />);

    fireEvent.click(screen.getByRole("button", { name: "Blood pressure" }));
    expect(document.querySelectorAll("circle.healthcurve-point--blood_pressure")).toHaveLength(4);
    expect(document.querySelectorAll("line.healthcurve-blood-pressure-link")).toHaveLength(2);
    expect(document.querySelector(".healthcurve-point--systolic")).not.toBeNull();
    expect(document.querySelector(".healthcurve-point--diastolic")).not.toBeNull();
    expect(screen.getByRole("img")).toHaveTextContent("Blood pressure 121/81 mmHg");
    const summaries = screen.getByLabelText("Series sample counts");
    expect(within(summaries).getByText("121/81 mmHg")).toBeVisible();
    expect(within(summaries).getByText("130/85 mmHg")).toBeVisible();
    expect(summaries).toHaveTextContent("121/81 mmHg · pulse 88 bpm");
    const { tooltip } = hoverAt(660);
    expect(within(tooltip).getAllByText("Blood pressure:")).toHaveLength(1);
    expect(tooltip).toHaveTextContent("Blood pressure: 121/81 mmHg");
    expect(tooltip).not.toHaveTextContent(/systolic point|diastolic point/);
    expect(tooltip).not.toHaveTextContent("88 bpm");
    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Blood-pressure pulse: 88 bpm");
  });

  it("shows temperature as discrete Fahrenheit-first facts with exact accessible values", () => {
    renderWithTheme(<DailyHealthCurve data={data({ temperature: [{
      id: "50000000-0000-4000-8000-000000000001",
      category: "fact",
      value: "38.00",
      unit: "c",
      normalized_c: "38.00",
      display_f: "100.4",
      display_c: "38.0",
      time: { occurred_at: "2026-03-08T16:00:00Z", local_time: "2026-03-08T12:00:00", timezone: "America/New_York", utc_offset_minutes: -240 },
      provenance: { ...provenance, source_type: "web", confirmation_state: "direct" },
      notes: null,
    }] })} />);

    fireEvent.click(screen.getByRole("button", { name: "Temperature" }));
    expect(document.querySelectorAll("circle.healthcurve-point--temperature")).toHaveLength(1);
    expect(screen.getByLabelText("Overlay series legend")).toHaveTextContent("Temperature · °F (°C)");
    const { tooltip } = hoverAt(660);
    expect(tooltip).toHaveTextContent("Temperature: 100.4 °F (38.0 °C)");
    const exactValues = screen.getByRole("region", { name: "Selected-day temperature exact values" });
    expect(exactValues).toHaveTextContent("100.4 °F (38.0 °C)");
    expect(screen.getByLabelText("Series sample counts")).toHaveTextContent("100.4 °F (38.0 °C)");
    const info = screen.getByRole("button", { name: "About Temperature data" });
    fireEvent.focus(info);
    expect(document.getElementById(info.getAttribute("aria-describedby") ?? "")).toHaveTextContent("1 recorded measurement(s)");
  });

  it("shows Garmin daily aggregates in their persistent series cards without inventing chart points", () => {
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
    const restingHeartRate: GarminRecord = {
      ...dailyStress,
      id: "20000000-0000-4000-8000-000000000004",
      summary: "Resting heart rate: 59 bpm",
      metric_type: "resting_heart_rate",
      value: "59",
      unit: "bpm",
      garmin_field_name: "restingHeartRate",
      measurement_label: "Resting heart rate",
      period_label: "daily summary",
    };
    const observedStress: GarminRecord = {
      ...sample(60),
      id: "20000000-0000-4000-8000-000000000005",
      metric_type: "stress",
      value: "44",
      unit: "garmin_score",
      sample_interval_seconds: 180,
    };
    renderWithTheme(<DailyHealthCurve data={data({ garmin: [dailyStress, nightlyHrv, wakingRespiration, restingHeartRate, observedStress] })} />);

    expect(document.querySelectorAll("circle.healthcurve-point--stress")).toHaveLength(0);
    expect(document.querySelectorAll("path.healthcurve-series--stress")).toHaveLength(0);
    expect(document.querySelectorAll("circle.healthcurve-point--hrv")).toHaveLength(0);
    expect(document.querySelectorAll("circle.healthcurve-point--respiration_rate")).toHaveLength(0);
    expect(screen.queryByRole("region", { name: "Garmin aggregate context" })).not.toBeInTheDocument();
    const summaries = screen.getByLabelText("Series sample counts");
    const stressCard = within(summaries).getByRole("heading", { name: /Garmin stress/ }).parentElement;
    expect(stressCard).toHaveTextContent("Stress: 31");
    expect(stressCard).toHaveTextContent("Observed average: 44");
    if (stressCard === null) throw new Error("Garmin stress summary missing");
    const aggregate = within(stressCard).getByText("Stress:").parentElement;
    const observed = within(stressCard).getByText("Observed average:").parentElement;
    if (aggregate === null || observed === null) throw new Error("Garmin stress values missing");
    expect(aggregate.compareDocumentPosition(observed) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(within(summaries).getByRole("heading", { name: /Heart rate/ }).parentElement).toHaveTextContent("Resting heart rate: 59 bpm");
    expect(within(summaries).getByRole("heading", { name: /^HRV/ }).parentElement).toHaveTextContent("Nightly average HRV: 41 ms");
    expect(within(summaries).getByRole("heading", { name: /Respiration/ }).parentElement).toHaveTextContent("Average waking respiration: 14.2 breaths/min");
    expect(summaries).not.toHaveTextContent(/untimed|exact point|gaps remain missing/);
    expect(hoverAt(0).tooltip).not.toHaveTextContent("Garmin stress: 31 score");
  });

  it("shows explicit sleep bounds and awake intervals without inferring wake timing", () => {
    const sleep: GarminRecord = {
      ...sample(0),
      id: "50000000-0000-4000-8000-000000000001",
      kind: "sleep",
      summary: "Sleep interval recorded by Garmin",
      time: { ...sample(0).time, occurred_at: "2026-03-08T06:00:00Z", local_time: "2026-03-08T01:00:00" },
      ended_at: "2026-03-08T12:00:00Z",
      duration_seconds: 21_600,
      duration_source: "provider",
      awakenings: 2,
      sleep_score: 82,
      sleep_intervals: [{ stage: "awake", started_at: "2026-03-08T08:00:00Z", ended_at: "2026-03-08T08:10:00Z" }],
    };
    const view = renderWithTheme(<DailyHealthCurve data={data({ garmin: [sleep] })} />);

    expect(document.querySelectorAll("[data-series='sleep']")).toHaveLength(1);
    expect(document.querySelectorAll(".healthcurve-sleep-band")).toHaveLength(1);
    expect(document.querySelectorAll(".healthcurve-sleep-marker--start")).toHaveLength(1);
    expect(document.querySelectorAll(".healthcurve-sleep-marker--end")).toHaveLength(1);
    expect(document.querySelectorAll(".healthcurve-awake-interval")).toHaveLength(1);
    expect(screen.getByLabelText("Overlay series legend")).toHaveTextContent("Sleep session");
    expect(screen.getByRole("img")).toHaveTextContent("explicit Garmin awake interval");
    expect(screen.queryByText(/Garmin reported one or more awakenings without their exact times/)).not.toBeInTheDocument();

    expect(hoverAt(60).tooltip).toHaveTextContent("Sleep: started");
    expect(screen.getByRole("tooltip").textContent.match(/GMT-5/g)).toHaveLength(1);
    expect(screen.getByRole("tooltip")).not.toHaveTextContent(/Garmin|provider|imported/i);
    expect(hoverAt(180).tooltip).toHaveTextContent("Awakening: started");
    expect(screen.getByRole("tooltip")).toHaveTextContent(/Awake interval: 10 minutes .*04:00–04:10 local/);
    expect(screen.getByRole("tooltip").textContent.match(/GMT-4/g)).toHaveLength(1);
    expect(hoverAt(185).tooltip).toHaveTextContent(/Awake interval: 10 minutes/);
    expect(hoverAt(179).tooltip).not.toHaveTextContent("Awake interval:");
    expect(hoverAt(190).tooltip).toHaveTextContent("Awakening: ended");
    expect(screen.getByRole("tooltip")).not.toHaveTextContent("Awake interval:");
    expect(hoverAt(420).tooltip).toHaveTextContent("Sleep: final wake / sleep ended");

    view.rerender(<DailyHealthCurve data={data({ garmin: [{ ...sleep, id: "50000000-0000-4000-8000-000000000002", sleep_intervals: [] }] })} />);
    expect(document.querySelectorAll(".healthcurve-awake-interval")).toHaveLength(0);
    fireEvent.click(screen.getByText("HealthCurve context and limits"));
    expect(screen.getByText(/Garmin reported one or more awakenings without their exact times/)).toBeVisible();
    expect(hoverAt(60).tooltip).toHaveTextContent("Awakenings: 2 reported; exact times unavailable");
  });

  it("consolidates duplicate, overlapping, and touching awake intervals", () => {
    const sleep: GarminRecord = {
      ...sample(0),
      id: "50000000-0000-4000-8000-000000000020",
      kind: "sleep",
      summary: "Sleep interval recorded by Garmin",
      time: { ...sample(0).time, occurred_at: "2026-03-08T06:00:00Z" },
      ended_at: "2026-03-08T12:00:00Z",
      awakenings: 3,
      sleep_intervals: [
        { stage: "awake", started_at: "2026-03-08T08:00:00Z", ended_at: "2026-03-08T08:10:00Z" },
        { stage: "awake", started_at: "2026-03-08T08:05:00Z", ended_at: "2026-03-08T08:15:00Z" },
        { stage: "awake", started_at: "2026-03-08T08:15:00Z", ended_at: "2026-03-08T08:20:00Z" },
      ],
    };
    const duplicate = {
      ...sleep,
      id: "50000000-0000-4000-8000-000000000021",
      sleep_intervals: [sleep.sleep_intervals?.[0]].filter((interval) => interval !== undefined),
    };
    renderWithTheme(<DailyHealthCurve data={data({ garmin: [sleep, duplicate] })} />);

    expect(document.querySelectorAll(".healthcurve-awake-interval")).toHaveLength(1);
    const tooltip = hoverAt(195).tooltip;
    expect(tooltip).toHaveTextContent(/Awake interval: 20 minutes/);
    expect(within(tooltip).getAllByText("Awake interval:")).toHaveLength(1);
    expect(hoverAt(200).tooltip).not.toHaveTextContent("Awake interval:");
  });

  it("clips overnight sleep and distinguishes multiple sessions in the tooltip", () => {
    const overnight: GarminRecord = {
      ...sample(0),
      id: "50000000-0000-4000-8000-000000000010",
      kind: "sleep",
      summary: "Overnight sleep",
      time: { ...sample(0).time, occurred_at: "2026-03-08T04:00:00Z" },
      ended_at: "2026-03-08T05:30:00Z",
      awakenings: 0,
      sleep_intervals: [],
    };
    const later: GarminRecord = {
      ...overnight,
      id: "50000000-0000-4000-8000-000000000011",
      summary: "Later sleep",
      time: { ...sample(0).time, occurred_at: "2026-03-08T15:00:00Z" },
      ended_at: "2026-03-08T16:00:00Z",
    };
    renderWithTheme(<DailyHealthCurve data={data({ garmin: [overnight, later] })} />);

    expect(document.querySelectorAll(".healthcurve-sleep-band")).toHaveLength(2);
    expect(document.querySelectorAll(".healthcurve-sleep-marker--start")).toHaveLength(1);
    expect(document.querySelectorAll(".healthcurve-sleep-marker--end")).toHaveLength(2);
    expect(hoverAt(30).tooltip).toHaveTextContent("Sleep: final wake / sleep ended");
    expect(hoverAt(600).tooltip).toHaveTextContent("Sleep: started");
    expect(hoverAt(660).tooltip).toHaveTextContent("Sleep: final wake / sleep ended");
  });

  it("shows distinct actual recorded doses in deterministic tooltip order", () => {
    const base = data();
    const marker = {
      dose_event_id: "90000000-0000-4000-8000-000000000002",
      occurred_at: "2026-03-08T06:00:00Z",
      local_time: "2026-03-08T01:00:00",
      timezone: "America/New_York",
      utc_offset_minutes: -300,
      medication_name: "Hydrocortisone",
      formulation: "conventional immediate-release tablet",
      amount: "10",
      unit: "mg" as const,
      route: "oral" as const,
      category: "scheduled" as const,
      source_type: "web" as const,
      confirmation_state: "direct" as const,
      supersedes_id: null,
      supported: true,
      exclusion_reason: null,
      carryover: false,
      modeled_peak_at: "2026-03-08T07:00:00Z",
    };
    renderWithTheme(<DailyHealthCurve data={data({ exposure: { ...base.exposure, dose_markers: [
      { ...marker, dose_event_id: "90000000-0000-4000-8000-000000000003", occurred_at: "2026-03-08T06:00:30Z", medication_name: "Prednisone", amount: "5", supported: false, exclusion_reason: "unsupported_medication", modeled_peak_at: null },
      marker,
      { ...marker, dose_event_id: "90000000-0000-4000-8000-000000000001", amount: "2.5", category: "stress" },
    ] } })} />);

    const tooltip = hoverAt(60).tooltip;
    const doseRows = within(tooltip).getAllByText(/(?:Regular|Stress) dose:/).map((label) => label.parentElement?.textContent);
    expect(doseRows).toEqual([
      "Stress dose: Hydrocortisone 2.5 mg",
      "Regular dose: Hydrocortisone 10 mg",
      "Regular dose: Prednisone 5 mg",
    ]);
    expect(tooltip.textContent.match(/GMT-5/g)).toHaveLength(1);
    expect(tooltip).not.toHaveTextContent(/manual|confirmed|source|provider/i);

    fireEvent.click(screen.getByRole("checkbox", { name: "Theoretical exposure and actual doses" }));
    expect(hoverAt(60).tooltip).not.toHaveTextContent(/Regular dose|Stress dose/);
  });

  it("renders empty context lanes and the true 23-hour DST axis without inventing data", () => {
    renderWithTheme(<DailyHealthCurve data={data()} />);

    expect(screen.getByText("23 hours")).toBeVisible();
    expect(document.querySelectorAll(".healthcurve-hour-mark")).toHaveLength(24);
    expect([...document.querySelectorAll(".healthcurve-time-label")].map((element) => element.textContent)).toEqual([
      "00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "00:00",
    ]);
    const summaries = screen.getByLabelText("Series sample counts");
    expect(within(summaries).getByRole("heading", { name: /Garmin stress/ }).parentElement).toHaveTextContent("No values recorded");
    fireEvent.focus(screen.getByRole("button", { name: "About Garmin stress data" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("0 exact point(s)");
    fireEvent.blur(screen.getByRole("button", { name: "About Garmin stress data" }));
    const info = screen.getByRole("button", { name: "About Heart rate data" });
    fireEvent.pointerEnter(info);
    expect(info).toHaveAttribute("aria-describedby", "curve-summary-metadata-heart_rate");
    expect(document.getElementById("curve-summary-metadata-heart_rate")).toHaveAttribute("role", "tooltip");
    fireEvent.pointerLeave(info);
    expect(info).not.toHaveAttribute("aria-describedby");
    fireEvent.click(screen.getByText("HealthCurve context and limits"));
    expect(screen.getByText(/expected missing counts are not invented/)).toBeVisible();
  });

  it("keeps hourly marks aligned through a 25-hour fall-back day", () => {
    const base = data();
    renderWithTheme(<DailyHealthCurve data={data({ exposure: {
      ...base.exposure,
      date: "2026-11-01",
      day_start: "2026-11-01T04:00:00Z",
      day_end: "2026-11-02T05:00:00Z",
      elapsed_hours: "25",
      samples: [],
    } })} />);

    expect(screen.getByText("25 hours")).toBeVisible();
    expect(document.querySelectorAll(".healthcurve-hour-mark")).toHaveLength(26);
    expect([...document.querySelectorAll(".healthcurve-time-label")].map((element) => element.textContent)).toEqual([
      "00:00", "04:00", "08:00", "12:00", "16:00", "20:00", "00:00",
    ]);
  });

  it("keeps a large sampled day available in the chart", () => {
    const garmin = Array.from({ length: 1_000 }, (_, index) => sample(index));
    renderWithTheme(<DailyHealthCurve data={data({ garmin })} />);

    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));
    expect(document.querySelectorAll("circle.healthcurve-point--heart_rate")).toHaveLength(0);
    expect(document.querySelectorAll("path.healthcurve-series--heart_rate").length).toBeGreaterThan(0);
  });

  it("shows close unscored symptoms as timed events without inventing severity", () => {
    renderWithTheme(<DailyHealthCurve data={data({ symptoms: [{
      id: "10000000-0000-4000-8000-000000000001",
      category: "fact",
      name: "Synthetic fatigue",
      severity: null,
      body_area: null,
      time: { occurred_at: "2026-03-08T16:00:00Z", local_time: "2026-03-08T12:00:00", timezone: "America/New_York", utc_offset_minutes: -240 },
      provenance: { ...provenance, source_type: "web", confirmation_state: "direct" },
      episode_id: null,
      notes: null,
    }, {
      id: "10000000-0000-4000-8000-000000000002",
      category: "fact",
      name: "Synthetic dizziness",
      severity: null,
      body_area: null,
      time: { occurred_at: "2026-03-08T16:01:00Z", local_time: "2026-03-08T12:01:00", timezone: "America/New_York", utc_offset_minutes: -240 },
      provenance: { ...provenance, source_type: "telegram", confirmation_state: "confirmed_from_draft" },
      episode_id: null,
      notes: null,
    }] })} />);

    fireEvent.click(screen.getByRole("button", { name: "Recorded events" }));
    expect(document.querySelectorAll("circle.healthcurve-point--symptoms")).toHaveLength(0);
    expect(document.querySelectorAll(".healthcurve-unscored-symptom-marker")).toHaveLength(2);
    expect(screen.getByRole("img")).toHaveAccessibleName(/2 recorded symptom events/i);
    const symptomList = screen.getByRole("heading", { name: "Recorded symptoms" }).parentElement;
    const chart = screen.getByRole("region", { name: "Daily HealthCurve synchronized chart" });
    expect(chart.compareDocumentPosition(symptomList as Node) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(symptomList).toHaveTextContent("Synthetic fatigue — severity not recorded");
    expect(symptomList).toHaveTextContent("Synthetic dizziness — severity not recorded");
    const summaryCards = screen.getByLabelText("Series sample counts");
    const summary = within(summaryCards).getByRole("heading", { name: /Symptoms/ }).parentElement;
    expect(summary).toHaveTextContent("Synthetic fatigue · severity not recorded");
    expect(summary).toHaveTextContent("Synthetic dizziness · severity not recorded");
    const info = screen.getByRole("button", { name: "About Symptoms data" });
    fireEvent.focus(info);
    expect(document.getElementById(info.getAttribute("aria-describedby") ?? "")).toHaveTextContent("2 recorded event(s); 0 with severity and 2 without severity");
    fireEvent.blur(info);
    const summaryCount = summaryCards.children.length;
    fireEvent.click(screen.getByRole("checkbox", { name: "Symptoms" }));
    expect(document.querySelectorAll(".healthcurve-unscored-symptom-marker")).toHaveLength(0);
    expect(screen.getByRole("heading", { name: "Recorded symptoms" })).toBeVisible();
    expect(summaryCards.children).toHaveLength(summaryCount);
    fireEvent.click(screen.getByRole("checkbox", { name: "Symptoms" }));
    const { tooltip } = hoverAt(660);
    expect(tooltip).toHaveTextContent("Synthetic fatigue: severity missing");
    expect(tooltip).toHaveTextContent("Synthetic dizziness: severity missing");
  });
});
