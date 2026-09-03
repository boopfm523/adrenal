import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { DailyPatterns } from "../api/client";
import { DailyPatternsTable } from "./DailyPatternsTable";

const metrics: DailyPatterns["longitudinal_summary"]["metrics"] = [
  { key: "exposure_auc_reu_hours", label: "Theoretical exposure AUC", unit: "REU-hours", observed_days: 30, missing_days: 0, observed_day_percent: "100", minimum: "0", median: "0", maximum: "244.817090855", first_observed: "0", last_observed: "73.8217", first_to_last_change: "73.8217", trend_eligible: true },
  { key: "average_symptom_severity", label: "Average recorded symptom severity", unit: "0-10", observed_days: 6, missing_days: 24, observed_day_percent: "20", minimum: "2", median: "3", maximum: "4", first_observed: "2", last_observed: "4", first_to_last_change: null, trend_eligible: false },
  { key: "garmin_stress_daily_average", label: "Garmin stress daily average", unit: "score", observed_days: 29, missing_days: 1, observed_day_percent: "96.6667", minimum: "14.375", median: "33.1846", maximum: "45.305", first_observed: "20", last_observed: "29.857", first_to_last_change: "9.857", trend_eligible: true },
  { key: "heart_rate_daily_average", label: "Heart rate daily average", unit: "bpm", observed_days: 29, missing_days: 1, observed_day_percent: "96.6667", minimum: "57.1964", median: "71.6815", maximum: "81.7211", first_observed: "65", last_observed: "71.4865", first_to_last_change: "6.4865", trend_eligible: true },
  { key: "hrv_daily_average", label: "HRV daily average", unit: "ms", observed_days: 30, missing_days: 0, observed_day_percent: "100", minimum: "22.0814", median: "32.0792", maximum: "44.08", first_observed: "35", last_observed: "34.3852", first_to_last_change: "-0.6148", trend_eligible: true },
  { key: "respiration_daily_average", label: "Respiration daily average", unit: "breaths/min", observed_days: 29, missing_days: 1, observed_day_percent: "96.6667", minimum: "12.9583", median: "13.9425", maximum: "17.0543", first_observed: "13", last_observed: "14.7749", first_to_last_change: "1.7749", trend_eligible: true },
];

const data = {
  date_from: "2026-08-01",
  date_to: "2026-08-30",
  timezone: "America/New_York",
  feature_version: "hc-daily-pattern-v1",
  safety_label: "Synthetic descriptive analytics.",
  definitions: {},
  exposure_model_versions: ["hc-exposure-v1"],
  longitudinal_summary: {
    total_days: 30,
    minimum_observed_days_for_trend: 7,
    coverage_definition: "Coverage describes recorded values.",
    multiple_comparison_caution: "Associations do not establish causation.",
    metrics,
    model_version_periods: [],
  },
  days: [],
} as DailyPatterns;

const dailyData = {
  ...data,
  days: [{
    date: "2026-08-05",
    timezone: "America/New_York",
    elapsed_hours: "24",
    feature_version: "hc-daily-pattern-v1",
    exposure_model_version: "hc-exposure-v1",
    dose_plan_version_ids: [],
    source_revision_watermark_sha256: "a".repeat(64),
    supported_dose_count: 0,
    excluded_dose_count: 0,
    exposure_peak_reu: "0",
    exposure_peak_at: "2026-08-05T04:00:00Z",
    exposure_auc_reu_hours: "0",
    symptom_count: 0,
    symptom_severity_sample_count: 0,
    symptom_severity_missing_count: 0,
    average_symptom_severity: null,
    symptom_timings: [],
    wearables: [
      { metric_type: "stress", unit: "score", sample_count: 414, samples_without_cadence: 1, observed_coverage_minutes: "1296", observed_coverage_percent: "90", missingness_state: "partial_observed_coverage", incompatible_units: false, minimum: "5", average: "25.744", maximum: "82", source_revision_watermark_sha256: "b".repeat(64), summary_version: "hc-wearable-daily-v1" },
      { metric_type: "heart_rate", unit: "bpm", sample_count: 713, samples_without_cadence: 1, observed_coverage_minutes: "1424", observed_coverage_percent: "98.8889", missingness_state: "partial_observed_coverage", incompatible_units: false, minimum: "53", average: "68.2342", maximum: "108", source_revision_watermark_sha256: "c".repeat(64), summary_version: "hc-wearable-daily-v1" },
      { metric_type: "hrv", unit: null, sample_count: 0, samples_without_cadence: 0, observed_coverage_minutes: "0", observed_coverage_percent: "0", missingness_state: "no_samples", incompatible_units: false, minimum: null, average: null, maximum: null, source_revision_watermark_sha256: "d".repeat(64), summary_version: "hc-wearable-daily-v1" },
      { metric_type: "respiration_rate", unit: null, sample_count: 0, samples_without_cadence: 0, observed_coverage_minutes: "0", observed_coverage_percent: "0", missingness_state: "no_samples", incompatible_units: false, minimum: null, average: null, maximum: null, source_revision_watermark_sha256: "e".repeat(64), summary_version: "hc-wearable-daily-v1" },
    ],
    blood_pressure: { sample_count: 0, pulse_sample_count: 0, pulse_missing_count: 0, systolic: { minimum: null, average: null, maximum: null }, diastolic: { minimum: null, average: null, maximum: null }, pulse: { minimum: null, average: null, maximum: null } },
    stress_episodes: { count: 0, open_count: 0, overlap_minutes: "0" },
  }],
} as DailyPatterns;

describe("DailyPatternsTable", () => {
  it("uses concise human-facing precision in the longitudinal summary", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("null", { headers: { "Content-Type": "application/json" } }));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><DailyPatternsTable data={data} /></MemoryRouter></QueryClientProvider>);

    const summary = screen.getByRole("region", { name: "Longitudinal pattern summary" });
    expect(within(summary).getByRole("rowheader", { name: /Theoretical exposure AUC/ }).parentElement).toHaveTextContent("244.8 REU-hours maximum");
    expect(within(summary).getByRole("rowheader", { name: /Average recorded symptom severity/ }).parentElement).toHaveTextContent("2 0-10 minimum; 3 0-10 median; 4 0-10 maximum");
    expect(within(summary).getByRole("rowheader", { name: /Garmin stress daily average/ }).parentElement).toHaveTextContent("29 of 30 days (97%)");
    expect(within(summary).getByRole("rowheader", { name: /Heart rate daily average/ }).parentElement).toHaveTextContent("57.2 bpm minimum; 71.7 bpm median; 81.7 bpm maximum");
    expect(within(summary).getByRole("rowheader", { name: /HRV daily average/ }).parentElement).toHaveTextContent("−0.6 ms");
    expect(within(summary).getByRole("rowheader", { name: /Respiration daily average/ }).parentElement).toHaveTextContent("+1.8 breaths/min");
  });

  it("rounds wearable averages and cadence coverage in daily detail rows", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("null", { headers: { "Content-Type": "application/json" } }));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><DailyPatternsTable data={dailyData} /></MemoryRouter></QueryClientProvider>);

    const details = screen.getByRole("region", { name: "Daily pattern values" });
    expect(details).toHaveTextContent("5 score–82 score; average 25.7 score; 414 samples; 90% observed cadence coverage");
    expect(details).toHaveTextContent("53 bpm–108 bpm; average 68.2 bpm; 713 samples; 99% observed cadence coverage");
    expect(details).not.toHaveTextContent("25.744");
    expect(details).not.toHaveTextContent("98.8889%");
  });
});
