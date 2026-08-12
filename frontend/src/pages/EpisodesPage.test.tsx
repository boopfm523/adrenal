import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../auth/context";
import { sessionStore } from "../api/session";
import { localDate, shiftIsoDate } from "../time";
import { EpisodesPage } from "./EpisodesPage";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "Europe/London" } };
const auth: AuthContextValue = { status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() };

function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
function response(body: unknown): Response { return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }); }
function page(items: unknown[], current = 1, totalPages = 1): Record<string, unknown> { return { items, page: { page: current, page_size: 25, total_items: totalPages * 25, total_pages: totalPages } }; }
function episode(status: "open" | "resolved") { return { id: "11111111-1111-4111-8111-111111111111", category: "fact", trigger: "Synthetic illness", status, severity: "moderate", started_at: "2026-08-09T09:00:00Z", ended_at: status === "resolved" ? "2026-08-09T12:00:00Z" : null, timezone: "Europe/London", highest_temperature_c: "38.2", illness_description: "Synthetic context", recovery_notes: null, outcome: null, notes: null, dose_count: 2, symptom_count: 1 }; }
function injection() { return { id: "33333333-3333-4333-8333-333333333333", category: "fact", medication_id: "44444444-4444-4444-8444-444444444444", amount: "100.0000", unit: "mg", route: "intramuscular", time: { occurred_at: "2026-08-09T10:00:00Z", local_time: "2026-08-09T11:00:00", timezone: "Europe/London", utc_offset_minutes: 60 }, provenance: { recorded_at: "2026-08-09T10:01:00Z", source_type: "web", confirmation_state: "direct", supersedes_id: null, correction_reason: null, is_correction: false }, injection_site: null, reason: "Synthetic emergency", injected_by: null, response: null, emergency_services_called: true, transported_to_hospital: false, episode_id: null }; }

describe("Episodes page", () => {
  beforeEach(() => { sessionStore.set(session); });
  afterEach(() => { sessionStore.clear(); vi.restoreAllMocks(); });

  it("labels linked doses as facts and creates, updates, and closes an episode", async () => {
    const requests: { url: string; method: string; body: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      requests.push({ url, method, body: init?.body === undefined ? null : JSON.parse(init.body as string) });
      if (method === "GET" && url.includes("/emergency-injections")) return Promise.resolve(response({ ...page([injection()]), revisions: [] }));
      if (method === "GET") return Promise.resolve(response(url.includes("status_filter=open") ? page([episode("open")]) : page([{ ...episode("resolved"), id: "22222222-2222-4222-8222-222222222222" }], url.includes("page=2") ? 2 : 1, 2)));
      return Promise.resolve(response(episode(method === "PATCH" && (JSON.parse(init?.body as string) as { status?: string }).status === "resolved" ? "resolved" : "open")));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter initialEntries={["/episodes?local_date_from=2026-08-09&local_date_to=2026-08-10&timezone=Europe%2FLondon"]}><EpisodesPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    expect(await screen.findByText(/A dose linked to an episode records what happened/)).toBeVisible();
    expect(await screen.findAllByText("2 linked doses")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Open episode records table" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Resolved episode records table" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Emergency injection records table" })).toBeVisible();
    expect(screen.getByLabelText("Trigger")).toHaveAccessibleDescription(/event or circumstance.*not a symptom or dosing instruction/i);
    expect(screen.getByLabelText("Severity")).toHaveAccessibleDescription(/not a clinical triage score/i);
    expect(screen.getByLabelText("Start time")).toHaveAccessibleDescription(/when the episode began/i);
    expect(screen.getByLabelText("IANA timezone")).toHaveAccessibleDescription(/leave your profile timezone/i);
    expect(screen.getByLabelText("Highest temperature (°C)")).toHaveAccessibleDescription(/degrees Celsius/i);
    expect(screen.getByLabelText("Illness or stress context")).toHaveAccessibleDescription(/illness, injury, procedure, exertion, travel, or emotional stress/i);
    expect(screen.getByLabelText("Notes")).toHaveAccessibleDescription(/record doses and symptoms in their own forms/i);
    const injectionRegion = screen.getByRole("region", { name: "Emergency injection records table" });
    expect(within(injectionRegion).getByText("100 mg")).toBeVisible();
    expect(within(injectionRegion).getByText("intramuscular")).toBeVisible();
    expect(requests.filter((request) => request.method === "GET").every((request) => request.url.includes("local_date_from=2026-08-09") && request.url.includes("timezone=Europe%2FLondon"))).toBe(true);
    fireEvent.change(screen.getByLabelText("Trigger"), { target: { value: "Synthetic travel stress" } });
    fireEvent.click(screen.getByRole("button", { name: "Open episode" }));
    await waitFor(() => { expect(requests.some((request) => request.method === "POST" && request.body !== null)).toBe(true); });
    const created = requests.find((request) => request.method === "POST")?.body as { time: { timezone: string } };
    expect(created.time.timezone).toBe("Europe/London");

    fireEvent.click(screen.getByRole("button", { name: "Add or update context" }));
    fireEvent.change(screen.getByLabelText("Outcome"), { target: { value: "Synthetic recovery" } });
    fireEvent.click(screen.getByRole("button", { name: "Save context" }));
    await waitFor(() => { expect(requests.some((request) => request.method === "PATCH" && (request.body as { outcome?: string }).outcome === "Synthetic recovery")).toBe(true); });
    fireEvent.click(screen.getByRole("button", { name: "Close episode" }));
    await waitFor(() => { expect(requests.some((request) => request.method === "PATCH" && (request.body as { status?: string }).status === "resolved")).toBe(true); });
    const closed = requests.find((request) => request.method === "PATCH" && (request.body as { status?: string }).status === "resolved")?.body as { ended_at: { timezone: string } };
    expect(closed.ended_at.timezone).toBe("Europe/London");
    fireEvent.click(within(screen.getByRole("navigation", { name: "Episode history pagination" })).getByRole("button", { name: "Next" }));
    await waitFor(() => { expect(requests.some((request) => request.url.includes("page=2") && request.url.includes("status_filter=resolved") && request.url.includes("local_date_from=2026-08-09"))).toBe(true); });
  });

  it("defaults to seven local days and applies quick days or all history", async () => {
    const requests: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      requests.push(requestUrl(input));
      return Promise.resolve(response(requestUrl(input).includes("/emergency-injections") ? { ...page([]), revisions: [] } : page([])));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter><EpisodesPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);
    const today = localDate(new Date(), "Europe/London");
    const sevenDayStart = shiftIsoDate(today, -6);

    expect(screen.getByLabelText("From date")).toHaveValue(sevenDayStart);
    expect(screen.getByLabelText("Through date")).toHaveValue(today);
    await waitFor(() => { expect(requests.some((url) => url.includes(`local_date_from=${sevenDayStart}`) && url.includes(`local_date_to=${today}`))).toBe(true); });

    const yesterday = shiftIsoDate(today, -1);
    fireEvent.click(within(screen.getByRole("group", { name: "Quick episode dates" })).getByRole("button", { name: "Yesterday" }));
    await waitFor(() => { expect(requests.some((url) => url.includes(`local_date_from=${yesterday}`) && url.includes(`local_date_to=${yesterday}`))).toBe(true); });
    expect(screen.getByLabelText("From date")).toHaveValue(yesterday);
    expect(screen.getByLabelText("Through date")).toHaveValue(yesterday);

    fireEvent.click(screen.getByRole("button", { name: "Clear history filters" }));
    expect(await screen.findByText(/Showing all history/)).toBeVisible();
    expect(screen.getByLabelText("From date")).toHaveValue("");
    expect(screen.getByLabelText("Through date")).toHaveValue("");
  });
});
