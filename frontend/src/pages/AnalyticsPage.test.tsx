import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../auth/context";
import { AnalyticsPage } from "./AnalyticsPage";

const auth: AuthContextValue = { status: "authenticated", session: { csrfToken: "synthetic", user: { email: "owner@example.test", displayName: null, defaultTimezone: "Europe/London" } }, signIn: vi.fn(), signOut: vi.fn() };
function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
function response(): Response { return new Response(JSON.stringify({ date_from: "2026-08-01", date_to: "2026-08-02", timezone: "Europe/London", daily_doses: { definition: "Daily definition; missing is not zero.", timezone: "Europe/London", sample_count: 1, missing_count: 1, days_without_approved_plan: 1, values: [{ date: "2026-08-01", planned_total: "20", actual_total: "20", recorded_dose_count: 1, unit: "mg", incompatible_units: false }, { date: "2026-08-02", planned_total: null, actual_total: null, recorded_dose_count: 0, unit: null, incompatible_units: false }] }, timing: { definition: "On time means within 30 minutes.", timezone: "Europe/London", sample_count: 2, missing_count: 1, matched_count: 1, on_time: 1, early: 0, late: 0, unplanned: 0, total_absolute_deviation_minutes: "12", average_absolute_deviation_minutes: "12", plan_periods: [{ regimen_version_id: "11111111-1111-4111-8111-111111111111", regimen_version_label: "Synthetic plan", effective_from: "2026-08-01T00:00:00", effective_to: null, sample_count: 2, matched_count: 1, missing_count: 1, on_time: 1, early: 0, late: 0, unplanned: 0, total_absolute_deviation_minutes: "12", average_absolute_deviation_minutes: "12" }] }, episodes: { definition: "Resolved duration only.", timezone: "Europe/London", sample_count: 1, missing_count: 1, count: 1, total_duration_minutes: "0", average_duration_minutes: null }, symptoms: { definition: "Average of recorded severity only.", timezone: "Europe/London", sample_count: 0, missing_count: 0, count: 0, average_severity: null, frequency: {} } }), { headers: { "Content-Type": "application/json" } }); }

describe("Analytics page", () => {
  it("renders definitions, timezone, missingness, and the no-causation caution", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => { urls.push(requestUrl(input)); return Promise.resolve(response()); });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter><AnalyticsPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    expect(screen.getByText(/Association does not establish causation/)).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Daily medication totals versus plan" })).toBeVisible();
    expect(screen.getAllByText("Europe/London").length).toBeGreaterThan(3);
    expect(screen.getAllByText("Gap—no value")).toHaveLength(2);
    expect(screen.getByText(/actual Missing—no dose facts/)).toBeInTheDocument();
    expect(screen.getByText(/planned Missing—no approved plan/)).toBeInTheDocument();
    expect(screen.getByText("X axis: Date (Europe/London). Y axis: Daily dose total (mg).")).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Daily medication totals versus plan data table" })).getAllByRole("cell", { name: "20" })).toHaveLength(2);
    expect(screen.getAllByText("Metric definition")).toHaveLength(4);
    expect(screen.getAllByText("Missing—no resolved durations")).toHaveLength(2);
    expect(screen.getByText("Missing—no severity values")).toBeVisible();
    expect(screen.getAllByText("12 minutes")).toHaveLength(3);
    const periods = screen.getByRole("region", { name: "Dose timing by historical plan period" });
    expect(within(periods).getByRole("rowheader", { name: "Synthetic plan" })).toBeVisible();
    expect(within(periods).getByText(/2026-08-01T00:00:00 through ongoing/)).toBeVisible();

    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-07-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Calculate metrics" }));
    await waitFor(() => { expect(urls.some((url) => url.includes("date_from=2026-07-01") && url.includes("timezone=Europe%2FLondon"))).toBe(true); });
  });
});
