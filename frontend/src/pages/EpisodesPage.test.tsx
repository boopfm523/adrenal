import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../auth/context";
import { sessionStore } from "../api/session";
import { EpisodesPage } from "./EpisodesPage";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "Europe/London" } };
const auth: AuthContextValue = { status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() };

function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
function response(body: unknown): Response { return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }); }
function episode(status: "open" | "resolved") { return { id: "11111111-1111-4111-8111-111111111111", category: "fact", trigger: "Synthetic illness", status, severity: "moderate", started_at: "2026-08-09T09:00:00Z", ended_at: status === "resolved" ? "2026-08-09T12:00:00Z" : null, timezone: "Europe/London", highest_temperature_c: "38.2", illness_description: "Synthetic context", recovery_notes: null, outcome: null, notes: null, dose_count: 2, symptom_count: 1 }; }

describe("Episodes page", () => {
  beforeEach(() => { sessionStore.set(session); });
  afterEach(() => { sessionStore.clear(); vi.restoreAllMocks(); });

  it("labels linked doses as facts and creates, updates, and closes an episode", async () => {
    const requests: { url: string; method: string; body: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      requests.push({ url, method, body: init?.body === undefined ? null : JSON.parse(init.body as string) });
      if (method === "GET") return Promise.resolve(response([episode("open"), { ...episode("resolved"), id: "22222222-2222-4222-8222-222222222222" }]));
      return Promise.resolve(response(episode(method === "PATCH" && (JSON.parse(init?.body as string) as { status?: string }).status === "resolved" ? "resolved" : "open")));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter><EpisodesPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    expect(await screen.findByText(/A dose linked to an episode records what happened/)).toBeVisible();
    expect(await screen.findAllByText("2 linked doses · 1 linked symptom")).toHaveLength(2);
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
  });
});
