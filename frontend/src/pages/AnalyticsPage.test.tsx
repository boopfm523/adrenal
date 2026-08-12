import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../auth/context";
import { localDate, shiftIsoDate } from "../time";
import { AnalyticsPage } from "./AnalyticsPage";

const auth: AuthContextValue = { status: "authenticated", session: { csrfToken: "synthetic", user: { email: "owner@example.test", displayName: null, defaultTimezone: "Europe/London" } }, signIn: vi.fn(), signOut: vi.fn() };
function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
const provenance = { recorded_at: "2026-08-01T08:01:00Z", source_type: "provider", confirmation_state: "provider_imported", supersedes_id: null, correction_reason: null, is_correction: false };
const eventTime = { occurred_at: "2026-08-01T08:00:00Z", local_time: "2026-08-01T09:00:00", timezone: "Europe/London", utc_offset_minutes: 60 };
const emptyPatternDay = { date: "2026-08-02", timezone: "Europe/London", elapsed_hours: "24", feature_version: "hc-daily-pattern-v1", exposure_model_version: "hc-exposure-v1", dose_plan_version_ids: [], source_revision_watermark_sha256: "b".repeat(64), supported_dose_count: 0, excluded_dose_count: 0, exposure_peak_reu: "0", exposure_peak_at: null, exposure_auc_reu_hours: "0", symptom_count: 0, symptom_severity_sample_count: 0, symptom_severity_missing_count: 0, average_symptom_severity: null, symptom_timings: [], wearables: [
  { metric_type: "stress", unit: null, sample_count: 0, samples_without_cadence: 0, observed_coverage_minutes: "0", observed_coverage_percent: "0", missingness_state: "no_samples", incompatible_units: false, minimum: null, average: null, maximum: null },
  { metric_type: "heart_rate", unit: null, sample_count: 0, samples_without_cadence: 0, observed_coverage_minutes: "0", observed_coverage_percent: "0", missingness_state: "no_samples", incompatible_units: false, minimum: null, average: null, maximum: null },
  { metric_type: "hrv", unit: null, sample_count: 0, samples_without_cadence: 0, observed_coverage_minutes: "0", observed_coverage_percent: "0", missingness_state: "no_samples", incompatible_units: false, minimum: null, average: null, maximum: null },
  { metric_type: "respiration_rate", unit: null, sample_count: 0, samples_without_cadence: 0, observed_coverage_minutes: "0", observed_coverage_percent: "0", missingness_state: "no_samples", incompatible_units: false, minimum: null, average: null, maximum: null },
], blood_pressure: { sample_count: 0, pulse_sample_count: 0, pulse_missing_count: 0, systolic: { minimum: null, average: null, maximum: null }, diastolic: { minimum: null, average: null, maximum: null }, pulse: { minimum: null, average: null, maximum: null } }, stress_episodes: { count: 0, open_count: 0, overlap_minutes: "0" } };
function jsonResponse(body: unknown): Response { return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }); }
function LocationProbe(): React.JSX.Element {
  const location = useLocation();
  const navigate = useNavigate();
  return <><output data-testid="location-search">{location.search}</output><button type="button" onClick={() => { void navigate(-1); }}>Test browser back</button></>;
}
function response(url: string, method = "GET"): Response {
  if (url.includes("/analytics/steroid-exposure")) return jsonResponse({ date: "2026-08-01", timezone: "Europe/London", day_start: "2026-07-31T23:00:00Z", day_end: "2026-08-01T23:00:00Z", elapsed_hours: "24", series_name: "Theoretical hydrocortisone exposure", series_unit: "REU", safety_label: "Theoretical hydrocortisone exposure—not a cortisol measurement or dosing guide.", definition: "Actual current doses produce a versioned relative exposure shape.", model: { version: "hc-exposure-v1", supported_medication: "hydrocortisone", supported_formulation: "conventional immediate-release tablet", supported_route: "oral", amount_unit: "mg", absorption_rate_per_hour: "2", elimination_half_life_hours: "1.7", elimination_rate_per_hour: "0.4077", peak_time_hours: "0.998", contribution_horizon_hours: 24, sample_interval_minutes: 5, references: [] }, dose_markers: [{ dose_event_id: "11111111-1111-4111-8111-111111111111", ...eventTime, medication_name: "Hydrocortisone", formulation: "conventional immediate-release tablet", amount: "10", unit: "mg", route: "oral", source_type: "manual", confirmation_state: "confirmed", supersedes_id: null, supported: true, exclusion_reason: null, carryover: false, modeled_peak_at: "2026-08-01T09:00:00Z" }], samples: [{ occurred_at: "2026-07-31T23:00:00Z", local_time: "2026-08-01T00:00:00", utc_offset_minutes: 60, theoretical_exposure_reu: "0" }, { occurred_at: "2026-08-01T09:00:00Z", local_time: "2026-08-01T10:00:00", utc_offset_minutes: 60, theoretical_exposure_reu: "10" }, { occurred_at: "2026-08-01T23:00:00Z", local_time: "2026-08-02T00:00:00", utc_offset_minutes: 60, theoretical_exposure_reu: "0.1" }], supported_dose_count: 1, excluded_dose_count: 0 });
  if (url.includes("/analytics/day-analysis")) return jsonResponse(null);
  if (url.includes("/analytics/pattern-analysis")) return jsonResponse(method === "POST" ? { outcome: "model_unavailable", detail: "The configured private model is unavailable. Deterministic results remain available.", analysis: null } : []);
  if (url.includes("/analytics/daily-patterns")) return jsonResponse({ date_from: "2026-08-01", date_to: "2026-08-02", timezone: "Europe/London", feature_version: "hc-daily-pattern-v1", safety_label: "Descriptive daily features do not establish causation or advise dosing.", definitions: { exposure: "Synthetic exposure definition.", wearable_coverage: "Synthetic observed coverage definition." }, exposure_model_versions: ["hc-exposure-v1"], longitudinal_summary: { total_days: 2, minimum_observed_days_for_trend: 7, coverage_definition: "Observed-day coverage is data availability only, not cortisol sufficiency.", multiple_comparison_caution: "Reviewing many metrics can surface chance patterns; correlation does not establish causation or diagnosis.", metrics: [{ key: "exposure_auc_reu_hours", label: "Theoretical exposure AUC", unit: "REU-hours", observed_days: 1, missing_days: 1, observed_day_percent: "50", minimum: "40", median: "40", maximum: "40", first_observed: "40", last_observed: "40", first_to_last_change: null, trend_eligible: false }], model_version_periods: [{ date_from: "2026-08-01", date_to: "2026-08-01", feature_version: "hc-daily-pattern-v1", exposure_model_version: "hc-exposure-v1" }] }, days: [{ date: "2026-08-01", timezone: "Europe/London", elapsed_hours: "24", feature_version: "hc-daily-pattern-v1", exposure_model_version: "hc-exposure-v1", dose_plan_version_ids: ["11111111-1111-4111-8111-111111111111"], source_revision_watermark_sha256: "a".repeat(64), supported_dose_count: 1, excluded_dose_count: 0, exposure_peak_reu: "10", exposure_peak_at: "2026-08-01T09:00:00Z", exposure_auc_reu_hours: "40", symptom_count: 1, symptom_severity_sample_count: 1, symptom_severity_missing_count: 0, average_symptom_severity: "7", symptom_timings: [{ symptom_event_id: "61111111-1111-4111-8111-111111111111", occurred_at: "2026-08-01T08:00:00Z", name: "Dizzy", severity: 7, previous_supported_dose_event_ids: ["11111111-1111-4111-8111-111111111111"], minutes_since_previous_supported_dose: "30", theoretical_exposure_reu: "8" }], wearables: [
    { metric_type: "stress", unit: "garmin_score", sample_count: 1, samples_without_cadence: 0, observed_coverage_minutes: "3", observed_coverage_percent: "0.2", missingness_state: "partial_observed_coverage", incompatible_units: false, minimum: "31", average: "31", maximum: "31" },
    { metric_type: "heart_rate", unit: "bpm", sample_count: 1, samples_without_cadence: 0, observed_coverage_minutes: "2", observed_coverage_percent: "0.1", missingness_state: "partial_observed_coverage", incompatible_units: false, minimum: "72", average: "72", maximum: "72" },
    { metric_type: "hrv", unit: null, sample_count: 0, samples_without_cadence: 0, observed_coverage_minutes: "0", observed_coverage_percent: "0", missingness_state: "no_samples", incompatible_units: false, minimum: null, average: null, maximum: null },
    { metric_type: "respiration_rate", unit: null, sample_count: 0, samples_without_cadence: 0, observed_coverage_minutes: "0", observed_coverage_percent: "0", missingness_state: "no_samples", incompatible_units: false, minimum: null, average: null, maximum: null },
  ], blood_pressure: { sample_count: 1, pulse_sample_count: 0, pulse_missing_count: 1, systolic: { minimum: "110", average: "110", maximum: "110" }, diastolic: { minimum: "70", average: "70", maximum: "70" }, pulse: { minimum: null, average: null, maximum: null } }, stress_episodes: { count: 1, open_count: 0, overlap_minutes: "60" } }, emptyPatternDay] });
  if (url.includes("/integrations/garmin/records")) return jsonResponse({ records: [
    { id: "20111111-1111-4111-8111-111111111111", kind: "daily", summary: "Stress: 28", time: { ...eventTime, occurred_at: "2026-07-31T23:00:00Z", local_time: "2026-08-01T00:00:00" }, provenance, metric_type: "stress", value: "28", unit: "garmin_score", aggregation: "daily_summary", sample_interval_seconds: null },
  ], page: { page: 1, page_size: 100, total_items: 1, total_pages: 1 }, notice: "Missing values remain unavailable." });
  if (url.includes("/integrations/garmin/samples")) return jsonResponse({ records: [
    { id: "21111111-1111-4111-8111-111111111111", kind: "sample", summary: "Stress: 31", time: eventTime, provenance, metric_type: "stress", value: "31", unit: "garmin_score", aggregation: "provider_sample", sample_interval_seconds: 180 },
    { id: "31111111-1111-4111-8111-111111111111", kind: "sample", summary: "Heart rate: 72", time: eventTime, provenance, metric_type: "heart_rate", value: "72", unit: "bpm", aggregation: "provider_sample", sample_interval_seconds: 120 },
    { id: "41111111-1111-4111-8111-111111111111", kind: "sample", summary: "HRV: 42", time: eventTime, provenance, metric_type: "hrv", value: "42", unit: "ms", aggregation: "provider_sample", sample_interval_seconds: 300 },
    { id: "51111111-1111-4111-8111-111111111111", kind: "sample", summary: "Respiration: 14", time: eventTime, provenance, metric_type: "respiration_rate", value: "14", unit: "breaths/min", aggregation: "provider_sample", sample_interval_seconds: 120 },
  ], page: { page: 1, page_size: 100, total_items: 4, total_pages: 1 }, notice: "Missing values remain unavailable." });
  if (url.includes("/integrations/garmin/sleep")) return jsonResponse({ records: [
    { id: "59111111-1111-4111-8111-111111111111", kind: "sleep", summary: "Sleep interval recorded by Garmin", time: { ...eventTime, occurred_at: "2026-08-01T00:00:00Z", local_time: "2026-08-01T01:00:00" }, provenance, ended_at: "2026-08-01T07:00:00Z", duration_seconds: 25200, duration_source: "provider", awakenings: 1, sleep_score: 82, sleep_intervals: [{ stage: "awake", started_at: "2026-08-01T03:00:00Z", ended_at: "2026-08-01T03:10:00Z" }] },
  ], page: { page: 1, page_size: 100, total_items: 1, total_pages: 1 }, notice: "Missing values remain unavailable." });
  if (url.includes("/symptoms?")) return jsonResponse({ items: [{ id: "61111111-1111-4111-8111-111111111111", category: "fact", name: "Dizzy", severity: 7, body_area: null, time: eventTime, provenance: { ...provenance, source_type: "manual", confirmation_state: "confirmed" }, episode_id: null, notes: null }], revisions: [], page: { page: 1, page_size: 100, total_items: 1, total_pages: 1 } });
  if (url.includes("/blood-pressure?")) return jsonResponse({ items: [{ id: "71111111-1111-4111-8111-111111111111", category: "fact", systolic_mmhg: 110, diastolic_mmhg: 70, pulse_bpm: 72, time: eventTime, provenance: { ...provenance, source_type: "manual", confirmation_state: "confirmed" }, notes: null }], revisions: [], page: { page: 1, page_size: 100, total_items: 1, total_pages: 1 } });
  if (url.includes("/temperature?")) return jsonResponse({ items: [{ id: "72111111-1111-4111-8111-111111111111", category: "fact", value: "38.00", unit: "c", normalized_c: "38.00", display_f: "100.4", display_c: "38.0", time: eventTime, provenance: { ...provenance, source_type: "web", confirmation_state: "direct" }, notes: null }], revisions: [], page: { page: 1, page_size: 100, total_items: 1, total_pages: 1 } });
  if (url.includes("/stress-episodes?")) return jsonResponse({ items: [{ id: "81111111-1111-4111-8111-111111111111", category: "fact", trigger: "Synthetic illness", status: "resolved", severity: "moderate", started_at: "2026-08-01T10:00:00Z", ended_at: "2026-08-01T11:00:00Z", timezone: "Europe/London", highest_temperature_c: null, illness_description: null, recovery_notes: null, outcome: null, notes: null, dose_count: 0, symptom_count: 1 }], page: { page: 1, page_size: 100, total_items: 1, total_pages: 1 } });
  return jsonResponse({ date_from: "2026-08-01", date_to: "2026-08-02", timezone: "Europe/London", daily_doses: { definition: "Daily definition; missing is not zero.", timezone: "Europe/London", sample_count: 1, missing_count: 1, days_without_approved_plan: 1, values: [{ date: "2026-08-01", planned_total: "20", actual_total: "20", recorded_dose_count: 1, unit: "mg", incompatible_units: false }, { date: "2026-08-02", planned_total: null, actual_total: null, recorded_dose_count: 0, unit: null, incompatible_units: false }] }, timing: { definition: "On time means within 30 minutes.", timezone: "Europe/London", sample_count: 2, missing_count: 1, matched_count: 1, on_time: 1, early: 0, late: 0, unplanned: 0, total_absolute_deviation_minutes: "12", average_absolute_deviation_minutes: "12", plan_periods: [{ regimen_version_id: "11111111-1111-4111-8111-111111111111", regimen_version_label: "Synthetic plan", effective_from: "2026-08-01T00:00:00", effective_to: null, sample_count: 2, matched_count: 1, missing_count: 1, on_time: 1, early: 0, late: 0, unplanned: 0, total_absolute_deviation_minutes: "12", average_absolute_deviation_minutes: "12" }] }, episodes: { definition: "Resolved duration only.", timezone: "Europe/London", sample_count: 1, missing_count: 1, count: 1, total_duration_minutes: "0", average_duration_minutes: null }, symptoms: { definition: "Average of recorded severity only.", timezone: "Europe/London", sample_count: 0, missing_count: 0, count: 0, average_severity: null, frequency: {} } });
}

describe("Analytics page", () => {
  it("renders definitions, timezone, missingness, and the no-causation caution", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => { const url = requestUrl(input); urls.push(url); return Promise.resolve(response(url, init?.method)); });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter initialEntries={["/healthcurve?day=2026-08-01&timezone=Europe%2FLondon"]}><AnalyticsPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    expect(screen.getByText(/Association does not establish causation/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "HealthCurve.ai", level: 1 })).toBeVisible();
    expect(screen.getByLabelText("HealthCurve date")).toHaveValue("2026-08-01");
    expect(await screen.findByRole("heading", { name: "Your daily HealthCurve" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Daily HealthCurve synchronized chart" })).toBeVisible();
    expect(screen.getByText(/Focused comparison on one time axis/)).toBeVisible();
    expect(screen.getByLabelText("Garmin stress")).toBeChecked();
    const seriesSummary = screen.getByLabelText("Series sample counts");
    expect(within(seriesSummary).getByText(/Garmin stress:/).parentElement).toHaveTextContent("1 exact point");
    expect(within(seriesSummary).getByText(/Symptoms:/).parentElement).toHaveTextContent("1 recorded event");
    expect(await screen.findByRole("heading", { name: "Daily medication totals versus plan" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Compare daily patterns" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Daily pattern exact values" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Longitudinal pattern summary" })).toBeVisible();
    expect(screen.getByRole("link", { name: "2026-08-01" })).toHaveAttribute("href", "/healthcurve?day=2026-08-01&timezone=Europe%2FLondon");
    expect(screen.getByText(/Withheld—fewer than 7 observed days/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Ask Ollama to explain these patterns" }));
    expect(await screen.findByText(/configured private model is unavailable/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Download daily features CSV" })).toBeVisible();
    expect(screen.getAllByText("No samples recorded")).toHaveLength(2);
    expect(screen.getByText(/1 empty date\(s\) hidden between 2026-08-02 and 2026-08-02/)).toBeVisible();
    expect(screen.queryByRole("link", { name: "2026-08-02" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 2 dates" }));
    expect(screen.getByRole("link", { name: "2026-08-02" })).toBeVisible();
    expect(screen.getByText("No modeled exposure")).toBeVisible();
    expect(screen.getByText("No doses recorded")).toBeVisible();
    expect(screen.getAllByText("GMT+1").length).toBeGreaterThan(3);
    expect(screen.getAllByText("Gap—no value")).toHaveLength(2);
    expect(screen.getByText(/actual Missing—no dose facts/)).toBeInTheDocument();
    expect(screen.getByText(/planned Missing—no approved plan/)).toBeInTheDocument();
    expect(screen.getByText("X axis: Date (GMT+1). Y axis: Daily dose total (mg).")).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Daily medication totals versus plan data table" })).getAllByRole("cell", { name: "20" })).toHaveLength(2);
    expect(screen.getAllByText("Metric definition")).toHaveLength(4);
    expect(screen.getAllByText("Missing—no resolved durations")).toHaveLength(2);
    expect(screen.getByText("Missing—no severity values")).toBeVisible();
    expect(screen.getAllByText("12 minutes")).toHaveLength(3);
    const periods = screen.getByRole("region", { name: "Dose timing by historical plan period" });
    expect(within(periods).getByRole("rowheader", { name: "Synthetic plan" })).toBeVisible();
    expect(within(periods).getByText(/2026-08-01T00:00:00 through ongoing/)).toBeVisible();

    fireEvent.click(screen.getByLabelText("Garmin stress"));
    expect(within(seriesSummary).getByText(/Garmin stress:/)).toBeVisible();
    expect(document.querySelectorAll("[data-series='stress']")).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-07-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Calculate metrics" }));
    await waitFor(() => { expect(urls.some((url) => url.includes("date_from=2026-07-01") && url.includes("timezone=Europe%2FLondon"))).toBe(true); });
  });

  it("loads recent local days immediately from accessible quick buttons", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => { const url = requestUrl(input); urls.push(url); return Promise.resolve(response(url, init?.method)); });
    const today = localDate(new Date(), "Europe/London");
    const yesterday = shiftIsoDate(today, -1);
    const twoDaysAgo = shiftIsoDate(today, -2);
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter initialEntries={["/healthcurve?day=2026-08-01&timezone=Europe%2FLondon"]}><AnalyticsPage /><LocationProbe /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    const shortcuts = screen.getByRole("group", { name: "Quick HealthCurve dates" });
    const yesterdayButton = within(shortcuts).getByRole("button", { name: "Yesterday" });
    await waitFor(() => { expect(screen.getByRole("heading", { name: "Your daily HealthCurve" })).toBeVisible(); });

    fireEvent.click(yesterdayButton);
    expect(screen.getByLabelText("HealthCurve date")).toHaveValue(yesterday);
    expect(within(screen.getByRole("group", { name: "Quick HealthCurve dates" })).getByRole("button", { name: "Yesterday" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("location-search")).toHaveTextContent(`day=${yesterday}`);
    await waitFor(() => { expect(urls.some((url) => url.includes(`day=${yesterday}`) && url.includes("timezone=Europe%2FLondon"))).toBe(true); });

    fireEvent.click(within(screen.getByRole("group", { name: "Quick HealthCurve dates" })).getByRole("button", { name: "2 days ago" }));
    expect(screen.getByLabelText("HealthCurve date")).toHaveValue(twoDaysAgo);
    expect(within(screen.getByRole("group", { name: "Quick HealthCurve dates" })).getByRole("button", { name: "2 days ago" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(screen.getByRole("group", { name: "Quick HealthCurve dates" })).getByRole("button", { name: "Today" }));
    expect(screen.getByLabelText("HealthCurve date")).toHaveValue(today);
    expect(within(screen.getByRole("group", { name: "Quick HealthCurve dates" })).getByRole("button", { name: "Today" })).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => { expect(screen.getByRole("heading", { name: "Your daily HealthCurve" })).toBeVisible(); });
    expect(screen.getByRole("button", { name: "Review next day" })).toBeDisabled();
  });

  it("navigates local calendar days, preserves chart focus, and honors browser history", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => { const url = requestUrl(input); urls.push(url); return Promise.resolve(response(url, init?.method)); });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter initialEntries={["/healthcurve?day=2026-08-01&timezone=Europe%2FLondon"]}><AnalyticsPage /><LocationProbe /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);
    await waitFor(() => { expect(screen.getByRole("heading", { name: "Your daily HealthCurve" })).toBeVisible(); });

    fireEvent.click(screen.getByRole("button", { name: "Heart rate" }));
    expect(screen.getByRole("checkbox", { name: "Heart rate" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Review previous day" }));
    expect(screen.getByLabelText("HealthCurve date")).toHaveValue("2026-07-31");
    expect(screen.getByTestId("location-search")).toHaveTextContent("day=2026-07-31");
    await waitFor(() => { expect(urls.some((url) => url.includes("day=2026-07-31") && url.includes("timezone=Europe%2FLondon"))).toBe(true); });
    await waitFor(() => { expect(screen.getByRole("heading", { name: "Your daily HealthCurve" })).toBeVisible(); });
    expect(screen.getByRole("checkbox", { name: "Heart rate" })).toBeChecked();

    fireEvent.click(screen.getByRole("button", { name: "Test browser back" }));
    await waitFor(() => { expect(screen.getByLabelText("HealthCurve date")).toHaveValue("2026-08-01"); });
    await waitFor(() => { expect(screen.getByRole("heading", { name: "Your daily HealthCurve" })).toBeVisible(); });
    expect(screen.getByTestId("location-search")).toHaveTextContent("day=2026-08-01");
    expect(screen.getByRole("checkbox", { name: "Heart rate" })).toBeChecked();
  });
});
