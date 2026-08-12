import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { AuthContext } from "../auth/context";
import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { ReportsPage } from "./ReportsPage";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "Europe/London" } };
function response(body: unknown, status = 200): Response { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
function url(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
function report(overrides: Record<string, unknown> = {}): Record<string, unknown> { return { id: "11111111-1111-4111-8111-111111111111", date_from: "2026-07-11", date_to: "2026-08-09", timezone: "Europe/London", selected_sections: ["metrics", "doses", "approved_plan"], include_ai: false, canonical_sha256: "a".repeat(64), render_version: "report-v1", created_at: "2026-08-09T12:00:00Z", artifacts: [{ format: "pdf", media_type: "application/pdf", sha256: "b".repeat(64), byte_size: 100, download_url: "/api/v1/reports/11111111-1111-4111-8111-111111111111/artifacts/pdf" }], ...overrides }; }
function reportPage(items: unknown[]): Record<string, unknown> { return { items, page: { page: 1, page_size: 25, total_items: items.length, total_pages: 1 } }; }
function preview(overrides: Record<string, unknown> = {}): Record<string, unknown> { return { ...report(), source_manifest: { fact: ["fact-1", "garmin-1", "garmin-2"], plan: ["plan-1"], patient_note: [], ai: [] }, metric_values: { daily_doses: { definition: "Synthetic deterministic sum", timezone: "Europe/London", values: [{ date: "2026-08-09", actual_total: "10.0000", planned_total: "10.0000", unit: "mg", recorded_dose_count: 1 }] } }, snapshot_content: { fact: [{ id: "fact-1", record_type: "dose", local_time: "2026-08-09T08:00:00", medication_name: "Synthetic hydrocortisone", amount: "10.0000", unit: "mg", route: "oral", category: "scheduled" }, { id: "garmin-1", record_type: "garmin_metric", local_time: "2026-08-09T09:00:00", metric_type: "heart_rate", value: "60", unit: "bpm" }, { id: "garmin-2", record_type: "garmin_metric", local_time: "2026-08-09T09:02:00", metric_type: "heart_rate", value: "70", unit: "bpm" }], plan: [{ id: "plan-1", record_type: "approved_regimen", version_label: "Synthetic plan", effective_from: "2026-08-01T00:00:00", slots: [] }], patient_note: [], ai: [] }, ...overrides }; }
function renderPage(initialEntry = "/reports"): void { sessionStore.set(session); render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter initialEntries={[initialEntry]}><AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}><ReportsPage /></AuthContext.Provider></MemoryRouter></QueryClientProvider></HealthCurveProvider>); }

describe("Reports page", () => {
  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); vi.setSystemTime(new Date("2026-08-09T12:00:00Z")); });
  afterEach(() => { sessionStore.clear(); vi.useRealTimers(); });

  it("filters snapshot creation history through shareable URL state", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => { const path = url(input); urls.push(path); return Promise.resolve(response(reportPage([report()]))); });
    renderPage("/reports?history_date_from=2026-08-09&history_date_to=2026-08-10&history_timezone=Europe%2FLondon");

    expect(await screen.findByRole("region", { name: "Immutable report snapshot history table" })).toBeVisible();
    expect(urls.some((path) => path.includes("local_date_from=2026-08-09") && path.includes("local_date_to=2026-08-10") && path.includes("timezone=Europe%2FLondon"))).toBe(true);
  });

  it("generates with AI off by default and previews separated immutable categories", async () => {
    const requests: { method: string; body: Record<string, unknown> | null }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => { const path = url(input); const method = init?.method ?? "GET"; requests.push({ method, body: typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : null }); if (method === "POST") return Promise.resolve(response(report(), 201)); if (path.endsWith("11111111-1111-4111-8111-111111111111")) return Promise.resolve(response(preview())); return Promise.resolve(response(reportPage([]))); });
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Loading report history");
    expect(await screen.findByRole("heading", { name: "No report snapshots yet" })).toBeVisible();
    expect(screen.getByLabelText("Include separately labeled AI-generated analysis")).not.toBeChecked();
    expect(document.querySelector(".report-layout")).not.toBeNull();
    await userEvent.click(screen.getByLabelText("Include CSV companion"));
    await userEvent.click(screen.getByRole("button", { name: "Generate immutable report" }));
    await waitFor(() => { expect(requests.some((request) => request.method === "POST")).toBe(true); });
    const submitted = requests.find((request) => request.method === "POST")?.body;
    expect(submitted).toEqual(expect.objectContaining({ include_ai: false, include_sensitive: false, companion_formats: ["csv"] }));
    expect(await screen.findByRole("heading", { name: "Recorded facts" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Physician-approved plan" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Patient notes and questions" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "AI-generated analysis" })).not.toBeInTheDocument();
    expect(screen.getByText("Synthetic hydrocortisone - 10 mg")).toBeVisible();
    expect(screen.getByText(/recorded 10 mg in 1 dose/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "heart rate — 2026-08-09" })).toBeVisible();
    expect(screen.getByText("average 65; low 60; high 70 bpm")).toBeVisible();
    expect(screen.getByText("2 samples")).toBeVisible();
    expect(screen.queryByText("heart rate: 60 bpm")).not.toBeInTheDocument();
    expect(screen.queryByText(/"record_type"/)).not.toBeInTheDocument();
    expect(screen.queryByText("fact-1")).not.toBeInTheDocument();
    expect(screen.queryByText(/10\.0000/)).not.toBeInTheDocument();
  });

  it("warns on AI opt-in and renders opted-in AI separately", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => { const path = url(input); if (path.endsWith("11111111-1111-4111-8111-111111111111")) return Promise.resolve(response(preview({ include_ai: true, source_manifest: { fact: [], plan: [], patient_note: [], ai: ["ai-1"] }, snapshot_content: { fact: [], plan: [], patient_note: [], ai: [{ id: "ai-1", body: "Synthetic generated observation" }] } }))); return Promise.resolve(response(reportPage([report({ include_ai: true })]))); });
    renderPage();
    await userEvent.click(screen.getByLabelText("Include separately labeled AI-generated analysis"));
    expect(screen.getByText(/AI content is generated, may be wrong/)).toBeVisible();
    await userEvent.click(await screen.findByRole("button", { name: "Preview snapshot" }));
    expect(await screen.findByRole("heading", { name: "AI-generated analysis" })).toBeVisible();
    expect(screen.getByText(/Generated content—not a recorded fact/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Download PDF" })).toHaveAttribute("href", expect.stringContaining("/artifacts/pdf"));
  });

  it("shows validation and load failures without claiming a report exists", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(response({ detail: "synthetic failure" }, 503));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Report history could not be loaded");
    fireEvent.change(screen.getByLabelText(/From date/), { target: { value: "2027-08-09" } });
    expect(screen.getByText("Through date must be on or after the from date.")).toBeVisible();
    for (const checkbox of screen.getAllByRole("checkbox").filter((control) => (control as HTMLInputElement).checked)) fireEvent.click(checkbox);
    expect(screen.getByText("Select at least one section.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Generate immutable report" })).toBeDisabled();
  });
});
