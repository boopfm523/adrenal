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
const garminProvenance = { ...provenance, source_type: "provider", confirmation_state: "provider_imported" };
const correctedGarminProvenance = { ...garminProvenance, supersedes_id: "88888888-8888-4888-8888-888888888888", correction_reason: "Synthetic provider revision", is_correction: true };
const garminRecords = {
  notice: "Unavailable provider values remain missing rather than zero.",
  page: { page: 1, page_size: 25, total_items: 4, total_pages: 1 },
  records: [
    { id: "55555555-5555-4555-8555-555555555555", kind: "daily", summary: "Steps", time, provenance: garminProvenance, metric_type: "steps", value: "8765.0000", unit: "steps", ended_at: null, duration_seconds: null, duration_source: null, awakenings: null, sleep_score: null, activity_type: null, distance_miles: null },
    { id: "99999999-9999-4999-8999-999999999999", kind: "daily", summary: "Stress", time: { ...time, occurred_at: "2026-08-09T13:00:00Z", local_time: "2026-08-09T09:00:00" }, provenance: garminProvenance, metric_type: "stress", value: "28.0000", unit: "garmin_score", ended_at: null, duration_seconds: null, duration_source: null, awakenings: null, sleep_score: null, activity_type: null, distance_miles: null },
    { id: "66666666-6666-4666-8666-666666666666", kind: "sleep", summary: "Sleep", time: { ...time, occurred_at: "2026-08-10T04:00:00Z", local_time: "2026-08-10T00:00:00" }, provenance: garminProvenance, metric_type: null, value: null, unit: null, ended_at: "2026-08-10T12:00:00Z", duration_seconds: 28800, duration_source: "provider", awakenings: 2, sleep_score: 82, activity_type: null, distance_miles: null },
    { id: "77777777-7777-4777-8777-777777777777", kind: "activity", summary: "Walking", time: { ...time, occurred_at: "2026-08-10T14:00:00Z", local_time: "2026-08-10T10:00:00" }, provenance: correctedGarminProvenance, metric_type: null, value: null, unit: null, ended_at: "2026-08-10T14:30:00Z", duration_seconds: 1800, duration_source: null, awakenings: null, sleep_score: null, activity_type: "walking", distance_miles: "3.1000" },
  ],
};

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function page(items: unknown[], revisions: unknown[] = []) {
  return { items, revisions, page: { page: 1, page_size: 25, total_items: items.length, total_pages: 1 } };
}

function renderPage(initialEntry = "/health-data"): void {
  sessionStore.set(session);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter initialEntries={[initialEntry]}><AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}><HealthDataPage /></AuthContext.Provider></MemoryRouter></QueryClientProvider>);
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
      if (url.includes("/integrations/garmin/records")) return Promise.resolve(json({ records: [], notice: "Synthetic", page: { page: 1, page_size: 25, total_items: 0, total_pages: 1 } }));
      return Promise.resolve(json(url.includes("blood-pressure") ? page([pressure]) : page([weight])));
    });
    renderPage();
    expect(await screen.findByRole("rowheader", { name: "118/76 mmHg" })).toBeVisible();

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
    const withoutPulse = { ...pressure, id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", systolic_mmhg: 112, diastolic_mmhg: 74, pulse_bpm: null, notes: "Synthetic seated reading", time: { ...time, occurred_at: "2026-08-08T12:15:00Z", local_time: "2026-08-08T08:15:00" }, provenance: { ...provenance, source_type: "web", confirmation_state: "direct" } };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (url.endsWith(`/blood-pressure/${corrected.id}/correct`) && init?.method === "POST") return Promise.resolve(json({ ...corrected, diastolic_mmhg: 77 }, 201));
      if (url.includes("/integrations/garmin/records")) return Promise.resolve(json(garminRecords));
      return Promise.resolve(json(url.includes("blood-pressure") ? page([corrected, withoutPulse], [pressure]) : page([kgWeight, weight])));
    });
    renderPage();
    expect(await screen.findByRole("img", { name: /Blood pressure/ })).toBeVisible();
    expect(screen.getByText("X axis: Experienced date / time (America/New_York). Y axis: Blood pressure (mmHg).")).toBeVisible();
    expect(screen.getByText("X axis: Experienced date / time (America/New_York). Y axis: Weight (lb).")).toBeVisible();
    expect(screen.getByRole("region", { name: "Blood pressure scrollable graph" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Weight scrollable graph" })).toBeVisible();
    expect(screen.getAllByText("Missing values")[0]).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Systolic (mmHg)" })).toBeInTheDocument();
    expect(screen.getAllByRole("columnheader", { name: "Weight (lb)" })).toHaveLength(1);
    expect(screen.getByRole("columnheader", { name: "Weight" })).toBeVisible();
    expect(screen.getAllByRole("cell", { name: "180" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("cell", { name: "183.2" }).length).toBeGreaterThan(0);
    const weightTable = screen.getByRole("region", { name: "Weight records table" });
    expect(within(weightTable).getByRole("cell", { name: /183\.2 lb83\.1 kg/ })).toBeVisible();
    expect(within(weightTable).queryByRole("columnheader", { name: "Entered value" })).not.toBeInTheDocument();
    expect(within(weightTable).getByRole("columnheader", { name: "Source" })).toBeVisible();
    expect(within(weightTable).getAllByRole("cell", { name: "Telegram" })).toHaveLength(2);
    const pressureTable = screen.getByRole("region", { name: "Blood pressure records table" });
    expect(pressureTable).toHaveAttribute("tabindex", "0");
    expect(within(pressureTable).getByText(/Current recorded blood-pressure facts in latest-experienced-time order/)).toBeVisible();
    expect(within(pressureTable).getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Experienced time", "Systolic / diastolic", "Pulse", "Source and confirmation", "Notes", "Action"]);
    const pressureRows = within(pressureTable).getAllByRole("row");
    const newestPressureRow = pressureRows[1];
    const olderPressureRow = pressureRows[2];
    if (newestPressureRow === undefined || olderPressureRow === undefined) throw new Error("Expected two blood-pressure record rows");
    expect(within(newestPressureRow).getByRole("rowheader", { name: "119/76 mmHg" })).toBeVisible();
    expect(within(olderPressureRow).getByRole("rowheader", { name: "112/74 mmHg" })).toBeVisible();
    expect(within(olderPressureRow).getByText("Not recorded")).toBeVisible();
    expect(within(olderPressureRow).getByText("Synthetic seated reading")).toBeVisible();
    expect(within(olderPressureRow).getByText("Web")).toBeVisible();
    expect(within(olderPressureRow).getByText("Direct")).toBeVisible();
    expect(within(newestPressureRow).getByText("Telegram")).toBeVisible();
    expect(within(newestPressureRow).getByText("Confirmed from draft")).toBeVisible();
    expect(within(pressureTable).getByText("Corrected · Synthetic correction")).toBeVisible();
    const garminTable = screen.getByRole("region", { name: "Garmin recorded observations table" });
    expect(within(garminTable).getByRole("cell", { name: /8,765 steps/ })).toBeVisible();
    expect(within(garminTable).getByRole("cell", { name: "Stress score: 28" })).toBeVisible();
    expect(within(garminTable).queryByText(/garmin_score/)).not.toBeInTheDocument();
    expect(within(garminTable).getByRole("cell", { name: /Sleep score: 82/ })).toBeVisible();
    expect(within(garminTable).getByRole("cell", { name: /Distance: 3\.1 mi/ })).toBeVisible();
    expect(within(garminTable).getByText(/Garmin provider-imported recorded facts/)).toBeVisible();
    expect(within(garminTable).queryByRole("columnheader", { name: "Source and provenance" })).not.toBeInTheDocument();
    expect(within(garminTable).queryByText("Garmin recorded observation")).not.toBeInTheDocument();
    expect(within(garminTable).queryByText(/Garmin · provider · provider imported/)).not.toBeInTheDocument();
    expect(within(garminTable).queryByText("Original provider record")).not.toBeInTheDocument();
    expect(within(garminTable).getByText("Provider correction · Synthetic provider revision")).toBeVisible();
    await userEvent.click(within(pressureTable).getByText("Revision history (1)"));
    expect(within(pressureTable).getByText(/118\/76 mmHg/)).toBeVisible();

    await userEvent.click(within(newestPressureRow).getByRole("button", { name: "Correct blood pressure" }));
    const form = screen.getByRole("form", { name: "Correct blood pressure" });
    const correctionRow = form.closest("tr");
    expect(correctionRow).not.toBeNull();
    expect(correctionRow?.querySelector("td")?.colSpan).toBe(6);
    const diastolic = within(form).getByLabelText("Diastolic (mmHg)");
    await userEvent.clear(diastolic);
    await userEvent.type(diastolic, "77");
    await userEvent.type(within(form).getByLabelText("Correction reason"), "Synthetic second correction");
    await userEvent.click(within(form).getByRole("button", { name: "Save corrected fact" }));
    await waitFor(() => { expect(fetchMock.mock.calls.some(([input, init]) => requestUrl(input).includes(`/blood-pressure/${corrected.id}/correct`) && init?.method === "POST")).toBe(true); });

    const kgRow = within(weightTable).getByRole("cell", { name: /183\.2 lb83\.1 kg/ }).closest("tr");
    if (kgRow === null) throw new Error("Expected the converted kilogram record row");
    await userEvent.click(within(kgRow).getByRole("button", { name: "Correct weight" }));
    expect(screen.getByRole("form", { name: "Correct weight" })).toBeVisible();
  });

  it("shares local-date filters in the URL and preserves them while paging", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      urls.push(url);
      const pageNumber = Number(new URL(url, "http://healthcurve.test").searchParams.get("page") ?? "1");
      const metadata = { page: pageNumber, page_size: 25, total_items: 50, total_pages: 2 };
      if (url.includes("/integrations/garmin/records")) return Promise.resolve(json({ ...garminRecords, page: metadata }));
      return Promise.resolve(json({ items: url.includes("blood-pressure") ? [pressure] : [weight], revisions: [], page: metadata }));
    });
    renderPage("/health-data?local_date_from=2026-08-09&local_date_to=2026-08-10&timezone=America%2FLos_Angeles");

    expect(await screen.findByLabelText("From date")).toHaveValue("2026-08-09");
    expect(screen.getByLabelText("Through date")).toHaveValue("2026-08-10");
    expect(screen.getByLabelText("IANA timezone")).toHaveValue("America/Los_Angeles");
    await waitFor(() => {
      expect(urls.filter((url) => url.includes("local_date_from=2026-08-09") && url.includes("local_date_to=2026-08-10") && url.includes("timezone=America%2FLos_Angeles"))).toHaveLength(3);
    });

    await userEvent.click(within(screen.getByRole("navigation", { name: "Blood pressure records pagination" })).getByRole("button", { name: "Next" }));
    await waitFor(() => {
      expect(urls.some((url) => url.includes("/blood-pressure?") && url.includes("page=2") && url.includes("local_date_from=2026-08-09") && url.includes("timezone=America%2FLos_Angeles"))).toBe(true);
    });
    expect(screen.getByLabelText("From date")).toHaveValue("2026-08-09");

    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByLabelText("From date")).toHaveValue("");
    expect(screen.getByLabelText("Through date")).toHaveValue("");
    expect(screen.getByLabelText("IANA timezone")).toHaveValue("America/New_York");
  });

  it("explains an invalid local-date range without issuing record requests", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.includes("/integrations/garmin/records")) return Promise.resolve(json({ records: [], notice: "Synthetic", page: { page: 1, page_size: 25, total_items: 0, total_pages: 1 } }));
      return Promise.resolve(json(page([])));
    });
    renderPage("/health-data?local_date_from=2026-08-11&local_date_to=2026-08-10&timezone=UTC");

    expect(await screen.findByRole("alert")).toHaveTextContent("From date must be on or before Through date.");
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await waitFor(() => { expect(fetchMock).toHaveBeenCalledTimes(3); });
  });
});
