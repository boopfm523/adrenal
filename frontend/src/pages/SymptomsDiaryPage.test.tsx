import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { SymptomsDiaryPage } from "./SymptomsDiaryPage";

const time = { occurred_at: "2026-08-09T13:00:00Z", local_time: "2026-08-09T09:00:00", timezone: "America/New_York", utc_offset_minutes: -240 };
const provenance = { recorded_at: "2026-08-09T13:01:00Z", source_type: "web", confirmation_state: "direct", supersedes_id: null, correction_reason: null, is_correction: false };

function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
function response(body: unknown, status = 200): Response { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }

describe("Symptoms and diary page", () => {
  afterEach(() => { sessionStore.clear(); });

  it("preserves symptom history and reveals sensitive text only after explicit action", async () => {
    sessionStore.set({ csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } });
    const prior = { id: "11111111-1111-4111-8111-111111111111", category: "fact", name: "Synthetic fatigue", severity: 4, body_area: null, time, provenance, episode_id: null, notes: null };
    const current = { ...prior, id: "22222222-2222-4222-8222-222222222222", severity: 6, provenance: { ...provenance, supersedes_id: prior.id, correction_reason: "Synthetic correction", is_correction: true } };
    const publicDiary = { id: "33333333-3333-4333-8333-333333333333", category: "fact", text: "Synthetic public note", is_sensitive: false, tags: null, time, provenance };
    const privateDiary = { ...publicDiary, id: "44444444-4444-4444-8444-444444444444", text: "Synthetic private note", is_sensitive: true };
    const publicLife = { id: "55555555-5555-4555-8555-555555555555", category: "fact", title: "Synthetic public event", life_category: "other", description: null, is_sensitive: false, time, provenance };
    const privateLife = { ...publicLife, id: "66666666-6666-4666-8666-666666666666", title: "Synthetic private event", is_sensitive: true };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (url.includes(`/symptoms/${current.id}/correct`) && init?.method === "POST") return Promise.resolve(response({ ...current, severity: 7 }, 201));
      if (url.includes("/symptoms")) return Promise.resolve(response([current, prior]));
      if (url.includes("/diary-events")) return Promise.resolve(response(url.includes("include_sensitive=true") ? [publicDiary, privateDiary] : [publicDiary]));
      if (url.includes("/life-events")) return Promise.resolve(response(url.includes("include_sensitive=true") ? [publicLife, privateLife] : [publicLife]));
      return Promise.resolve(response({ detail: "not found" }, 404));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter><SymptomsDiaryPage /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByText("Synthetic public note")).toBeVisible();
    expect(screen.getByText("Synthetic public event")).toBeVisible();
    expect(screen.queryByText("Synthetic private note")).not.toBeInTheDocument();
    expect(screen.queryByText("Synthetic private event")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("Show superseded value"));
    expect(screen.getByText(/severity 4\/10/)).toBeVisible();

    await userEvent.click(screen.getByRole("checkbox", { name: "Reveal sensitive diary and life-event entries" }));
    expect(await screen.findByText("Synthetic private note")).toBeVisible();
    expect(await screen.findByText("Synthetic private event")).toBeVisible();
    expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("include_sensitive=true"))).toBe(true);

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
  });
});
