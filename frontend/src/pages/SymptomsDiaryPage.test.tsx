import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { AuthContext } from "../auth/context";
import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { localDate, shiftIsoDate } from "../time";
import { SymptomsDiaryPage } from "./SymptomsDiaryPage";

const time = { occurred_at: "2026-08-09T13:00:00Z", local_time: "2026-08-09T09:00:00", timezone: "America/New_York", utc_offset_minutes: -240 };
const provenance = { recorded_at: "2026-08-09T13:01:00Z", source_type: "web", confirmation_state: "direct", supersedes_id: null, correction_reason: null, is_correction: false };

function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
function response(body: unknown, status = 200): Response { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
function factPage(items: unknown[]): Record<string, unknown> { return { items, revisions: [], page: { page: 1, page_size: 25, total_items: items.length, total_pages: 1 } }; }

describe("Symptoms and diary page", () => {
  afterEach(() => { sessionStore.clear(); });

  it("preserves symptom history and reveals sensitive text only after explicit action", async () => {
    const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } };
    sessionStore.set(session);
    const prior = { id: "11111111-1111-4111-8111-111111111111", category: "fact", name: "Synthetic fatigue", severity: 4, body_area: null, time, provenance, episode_id: null, notes: null };
    const current = { ...prior, id: "22222222-2222-4222-8222-222222222222", severity: 6, provenance: { ...provenance, supersedes_id: prior.id, correction_reason: "Synthetic correction", is_correction: true } };
    const publicDiary = { id: "33333333-3333-4333-8333-333333333333", category: "fact", text: "Synthetic public note", is_sensitive: false, tags: null, time, provenance };
    const privateDiary = { ...publicDiary, id: "44444444-4444-4444-8444-444444444444", text: "Synthetic private note", is_sensitive: true };
    const publicLife = { id: "55555555-5555-4555-8555-555555555555", category: "fact", title: "Synthetic public event", life_category: "other", description: null, is_sensitive: false, time, provenance };
    const privateLife = { ...publicLife, id: "66666666-6666-4666-8666-666666666666", title: "Synthetic private event", is_sensitive: true };
    const meal = { id: "88888888-8888-4888-8888-888888888888", category: "fact", size: null, time, provenance, notes: null };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (url.includes(`/symptoms/${current.id}/correct`) && init?.method === "POST") return Promise.resolve(response({ ...current, severity: 7 }, 201));
      if (url.endsWith("/symptoms") && init?.method === "POST") return Promise.resolve(response({ ...current, id: "77777777-7777-4777-8777-777777777777", name: "Synthetic nausea", severity: 3 }, 201));
      if (url.includes("/symptoms")) { const pageNumber = Number(new URL(url, "http://healthcurve.test").searchParams.get("page") ?? "1"); return Promise.resolve(response({ items: [current], revisions: [prior], page: { page: pageNumber, page_size: 25, total_items: 50, total_pages: 2 } })); }
      if (url.includes("/diary-events")) return Promise.resolve(response(factPage(url.includes("include_sensitive=true") ? [publicDiary, privateDiary] : [publicDiary])));
      if (url.endsWith("/meal-events") && init?.method === "POST") return Promise.resolve(response({ ...meal, id: "99999999-9999-4999-8999-999999999999", size: "l" }, 201));
      if (url.includes("/meal-events")) return Promise.resolve(response(factPage([meal])));
      if (url.includes("/life-events")) return Promise.resolve(response(factPage(url.includes("include_sensitive=true") ? [publicLife, privateLife] : [publicLife])));
      return Promise.resolve(response({ detail: "not found" }, 404));
    });
    render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter><AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}><SymptomsDiaryPage /></AuthContext.Provider></MemoryRouter></QueryClientProvider></HealthCurveProvider>);

    expect(await screen.findByText("Synthetic public note")).toBeVisible();
    expect(screen.getByText("Synthetic public event")).toBeVisible();
    const symptomTableRegion = screen.getByRole("region", { name: "Symptom records table" });
    expect(symptomTableRegion).toBeVisible();
    expect(within(symptomTableRegion).getByRole("table")).toHaveClass("symptom-records-table");
    expect(screen.getByRole("region", { name: "Diary records table" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Life event records table" })).toBeVisible();
    expect(screen.queryByText("Synthetic private note")).not.toBeInTheDocument();
    expect(screen.queryByText("Synthetic private event")).not.toBeInTheDocument();
    const today = localDate(new Date(), "America/New_York");
    expect(screen.getByLabelText("From date")).toHaveValue(shiftIsoDate(today, -6));
    expect(screen.getByLabelText("Through date")).toHaveValue(today);
    expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes(`local_date_from=${shiftIsoDate(today, -6)}`) && requestUrl(input).includes(`local_date_to=${today}`))).toBe(true);
    const twoDaysAgo = shiftIsoDate(today, -2);
    await userEvent.click(within(screen.getByRole("group", { name: "Quick symptom, meal, and diary dates" })).getByRole("button", { name: "2 days ago" }));
    await waitFor(() => { expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes(`local_date_from=${twoDaysAgo}`) && requestUrl(input).includes(`local_date_to=${twoDaysAgo}`))).toBe(true); });

    const createForm = screen.getByRole("form", { name: "Record a symptom" });
    await userEvent.type(within(createForm).getByLabelText("Symptom"), "Synthetic nausea");
    await userEvent.type(within(createForm).getByLabelText("Severity (0–10)"), "3");
    await userEvent.clear(within(createForm).getByLabelText("Experienced local time"));
    await userEvent.type(within(createForm).getByLabelText("Experienced local time"), "2026-08-10T14:05");
    await userEvent.type(within(createForm).getByLabelText("Notes"), "Synthetic web form test");
    await userEvent.click(within(createForm).getByRole("button", { name: "Record symptom" }));
    await waitFor(() => { expect(fetchMock.mock.calls.some(([input, init]) => requestUrl(input).endsWith("/symptoms") && init?.method === "POST")).toBe(true); });
    const createWrite = fetchMock.mock.calls.find(([input, init]) => requestUrl(input).endsWith("/symptoms") && init?.method === "POST");
    expect(JSON.parse(typeof createWrite?.[1]?.body === "string" ? createWrite[1].body : "{}") as unknown).toEqual({ name: "Synthetic nausea", severity: 3, body_area: null, time: { local_time: "2026-08-10T14:05", timezone: "America/New_York", fold: null }, ended_at: null, episode_id: null, notes: "Synthetic web form test" });
    expect(await screen.findByText("Symptom recorded.")).toBeVisible();
    expect(within(createForm).getByLabelText("Symptom")).toHaveValue("");

    const mealForm = screen.getByRole("form", { name: "Record a meal" });
    await userEvent.selectOptions(within(mealForm).getByLabelText("Meal size"), "l");
    await userEvent.clear(within(mealForm).getByLabelText("Meal experienced local time"));
    await userEvent.type(within(mealForm).getByLabelText("Meal experienced local time"), "2026-08-10T12:30");
    await userEvent.click(within(mealForm).getByRole("button", { name: "Record meal" }));
    await waitFor(() => { expect(fetchMock.mock.calls.some(([input, init]) => requestUrl(input).endsWith("/meal-events") && init?.method === "POST")).toBe(true); });
    const mealWrite = fetchMock.mock.calls.find(([input, init]) => requestUrl(input).endsWith("/meal-events") && init?.method === "POST");
    expect(JSON.parse(typeof mealWrite?.[1]?.body === "string" ? mealWrite[1].body : "{}") as unknown).toEqual({ size: "l", time: { local_time: "2026-08-10T12:30", timezone: "America/New_York", fold: null }, notes: null });
    expect(await screen.findByText("Meal recorded.")).toBeVisible();
    await userEvent.click(screen.getByText("Revision history (1)"));
    expect(screen.getByText(/severity 4\/10/)).toBeVisible();

    await userEvent.clear(screen.getByLabelText("From date"));
    await userEvent.type(screen.getByLabelText("From date"), "2026-08-09");
    await userEvent.clear(screen.getByLabelText("Through date"));
    await userEvent.type(screen.getByLabelText("Through date"), "2026-08-10");
    await userEvent.click(screen.getByRole("checkbox", { name: "Reveal sensitive diary and life-event entries" }));
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByText("Synthetic private note")).toBeVisible();
    expect(await screen.findByText("Synthetic private event")).toBeVisible();
    expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("include_sensitive=true"))).toBe(true);
    expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("local_date_from=2026-08-09") && requestUrl(input).includes("local_date_to=2026-08-10") && requestUrl(input).includes("timezone=America%2FNew_York"))).toBe(true);
    expect(fetchMock.mock.calls.every(([input]) => !requestUrl(input).includes("Synthetic private"))).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Correct recorded symptom" }));
    const form = screen.getByRole("form", { name: "Correct Synthetic fatigue symptom" });
    const severity = within(form).getByLabelText("Severity (0–10)");
    await userEvent.clear(severity);
    await userEvent.type(severity, "7");
    await userEvent.type(within(form).getByLabelText("Correction reason"), "Synthetic second correction");
    await userEvent.click(within(form).getByRole("button", { name: "Save corrected fact" }));
    await waitFor(() => { expect(fetchMock.mock.calls.some(([input, init]) => requestUrl(input).includes(`/symptoms/${current.id}/correct`) && init?.method === "POST")).toBe(true); });
    const write = fetchMock.mock.calls.find(([input, init]) => requestUrl(input).includes(`/symptoms/${current.id}/correct`) && init?.method === "POST");
    const body = write?.[1]?.body;
    expect(JSON.parse(typeof body === "string" ? body : "{}") as unknown).toEqual({ reason: "Synthetic second correction", changes: { severity: 7 } });

    await userEvent.click(within(screen.getByRole("navigation", { name: "Symptom records pagination" })).getByRole("button", { name: "Next" }));
    await waitFor(() => { expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("/symptoms?") && requestUrl(input).includes("page=2") && requestUrl(input).includes("local_date_from=2026-08-09"))).toBe(true); });

    const callsBeforeClear = fetchMock.mock.calls.length;
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText(/Showing all history/)).toBeVisible();
    expect(screen.getByLabelText("From date")).toHaveValue("");
    expect(screen.getByLabelText("Through date")).toHaveValue("");
    await waitFor(() => { expect(fetchMock.mock.calls.slice(callsBeforeClear).some(([input]) => !requestUrl(input).includes("local_date_from"))).toBe(true); });
  });
});
