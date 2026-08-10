import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { AuthContext } from "../auth/context";
import { HealthDataPage } from "./HealthDataPage";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } };
const time = { occurred_at: "2026-08-09T12:15:00Z", local_time: "2026-08-09T08:15:00", timezone: "America/New_York", utc_offset_minutes: -240 };
const provenance = { recorded_at: "2026-08-09T12:16:00Z", source_type: "telegram", confirmation_state: "confirmed_from_draft", supersedes_id: null, correction_reason: null, is_correction: false };
const pressure = { id: "11111111-1111-4111-8111-111111111111", category: "fact", systolic_mmhg: 118, diastolic_mmhg: 76, pulse_bpm: 62, time, provenance, notes: null };
const weight = { id: "22222222-2222-4222-8222-222222222222", category: "fact", value: "180.0000", unit: "lb", normalized_kg: "81.6466", display_lb: "180.0", time, provenance, notes: null };
const kgWeight = { ...weight, id: "44444444-4444-4444-8444-444444444444", value: "83.1000", unit: "kg", normalized_kg: "83.1000", display_lb: "183.2", time: { ...time, occurred_at: "2026-08-10T12:15:00Z", local_time: "2026-08-10T08:15:00" } };

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage(): void {
  sessionStore.set(session);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter><AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}><HealthDataPage /></AuthContext.Provider></MemoryRouter></QueryClientProvider>);
}

describe("Health data page", () => {
  afterEach(() => { sessionStore.clear(); });

  it("provides mobile-friendly direct entry and sends provenance-safe facts", async () => {
    const writes: { url: string; body: unknown; csrf: string | null }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (init?.method === "POST") {
        const rawBody = typeof init.body === "string" ? init.body : "{}";
        writes.push({ url, body: JSON.parse(rawBody) as unknown, csrf: new Headers(init.headers).get("X-CSRF-Token") });
        return Promise.resolve(json(url.endsWith("/weight") ? weight : pressure, 201));
      }
      return Promise.resolve(json(url.includes("blood-pressure") ? [pressure] : [weight]));
    });
    renderPage();
    expect(await screen.findByRole("heading", { name: "118/76 mmHg · pulse 62 bpm" })).toBeVisible();

    const bpForm = screen.getByRole("form", { name: "Record blood pressure" });
    await userEvent.type(within(bpForm).getByLabelText("Systolic (mmHg)"), "120");
    await userEvent.type(within(bpForm).getByLabelText("Diastolic (mmHg)"), "80");
    await userEvent.click(within(bpForm).getByRole("button", { name: "Record blood pressure" }));
    const weightForm = screen.getByRole("form", { name: "Record weight" });
    await userEvent.type(within(weightForm).getByLabelText("Value"), "181");
    await userEvent.click(within(weightForm).getByRole("button", { name: "Record weight" }));

    await waitFor(() => { expect(writes).toHaveLength(2); });
    expect(writes[0]).toEqual(expect.objectContaining({ url: "/api/v1/blood-pressure", csrf: "synthetic-csrf", body: expect.objectContaining({ systolic_mmhg: 120, diastolic_mmhg: 80, time: expect.objectContaining({ timezone: "America/New_York" }) }) }));
    expect(writes[1]).toEqual(expect.objectContaining({ url: "/api/v1/weight", csrf: "synthetic-csrf", body: expect.objectContaining({ value: "181", unit: "lb" }) }));
  });

  it("renders equivalent trend tables, explicit units, missingness, and correction provenance", async () => {
    const corrected = { ...pressure, id: "33333333-3333-4333-8333-333333333333", systolic_mmhg: 119, provenance: { ...provenance, supersedes_id: pressure.id, correction_reason: "Synthetic correction", is_correction: true } };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (url.endsWith(`/blood-pressure/${corrected.id}/correct`) && init?.method === "POST") return Promise.resolve(json({ ...corrected, diastolic_mmhg: 77 }, 201));
      return Promise.resolve(json(url.includes("blood-pressure") ? [corrected, pressure] : [kgWeight, weight]));
    });
    renderPage();
    expect(await screen.findByRole("img", { name: /Blood pressure/ })).toBeVisible();
    expect(screen.getAllByText("Missing values")[0]).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Systolic (mmHg)" })).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader", { name: "Weight (lb)" })).toHaveLength(2);
    expect(screen.getAllByRole("cell", { name: "180.0" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("cell", { name: "183.2" }).length).toBeGreaterThan(0);
    const weightTable = screen.getByRole("region", { name: "Weight records table" });
    expect(within(weightTable).getByRole("cell", { name: /83\.1000 kg/ })).toBeVisible();
    expect(within(weightTable).getByText("83.1000 kg normalized")).toBeVisible();
    expect(screen.getByText("Corrected · Synthetic correction")).toBeVisible();
    expect(screen.getAllByText(/Source: telegram · confirmed from draft/)).toHaveLength(2);
    await userEvent.click(screen.getByText("Revision history (1)"));
    expect(screen.getByText(/118\/76 mmHg/)).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Correct blood pressure" }));
    const form = screen.getByRole("form", { name: "Correct blood pressure" });
    const diastolic = within(form).getByLabelText("Diastolic (mmHg)");
    await userEvent.clear(diastolic);
    await userEvent.type(diastolic, "77");
    await userEvent.type(within(form).getByLabelText("Correction reason"), "Synthetic second correction");
    await userEvent.click(within(form).getByRole("button", { name: "Save corrected fact" }));
    await waitFor(() => { expect(fetchMock.mock.calls.some(([input, init]) => requestUrl(input).includes(`/blood-pressure/${corrected.id}/correct`) && init?.method === "POST")).toBe(true); });

    const kgRow = within(weightTable).getByRole("cell", { name: "183.2 lb" }).closest("tr");
    if (kgRow === null) throw new Error("Expected the converted kilogram record row");
    await userEvent.click(within(kgRow).getByRole("button", { name: "Correct weight" }));
    expect(screen.getByRole("form", { name: "Correct weight" })).toBeVisible();
  });
});
