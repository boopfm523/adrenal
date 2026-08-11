import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { AuthContext } from "../auth/context";
import { TodayPage } from "./TodayPage";

const session = {
  csrfToken: "synthetic-csrf",
  user: {
    email: "owner@example.test",
    displayName: "Synthetic Owner",
    defaultTimezone: "America/New_York",
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function comparison(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    date: "2026-08-09",
    timezone: "America/New_York",
    regimen_version_id: null,
    regimen_version_label: null,
    regimen_versions: [],
    slots: [],
    planned_total: null,
    actual_total: "0.0000",
    unplanned_doses: 0,
    missed_slots: 0,
    metric_definition: "Missing means no matching record; no zero dose is stored.",
    ...overrides,
  };
}

function plannedSlot(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    slot_id: "11111111-1111-4111-8111-111111111111",
    medication_id: "22222222-2222-4222-8222-222222222222",
    medication_name: "Synthetic medicine",
    scheduled_local_time: "07:00:00",
    planned_amount: "10.0000",
    actual_amount: null,
    actual_local_time: null,
    dose_id: null,
    status: "missing",
    minutes_from_scheduled: null,
    unit: "mg",
    route: "oral",
    ...overrides,
  };
}

function renderToday(): void {
  sessionStore.set(session);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AuthContext.Provider value={{
          status: "authenticated",
          session,
          signIn: vi.fn(),
          signOut: vi.fn(),
        }}>
          <TodayPage />
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Today page", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date("2026-08-09T15:30:00Z"));
  });

  afterEach(() => {
    sessionStore.clear();
    vi.useRealTimers();
  });

  it("is useful without an approved plan or recorded doses", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.includes("plan-comparison")) return Promise.resolve(jsonResponse(comparison()));
      if (url.includes("stress-episodes")) return Promise.resolve(jsonResponse([]));
      return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
    });

    renderToday();

    expect(await screen.findByRole("heading", { name: "No approved plan for this date" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "No doses recorded today" })).toBeVisible();
    expect(screen.getByText("This is an empty record, not a recorded amount of zero.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Open emergency plan" })).toHaveAttribute("href", "/emergency");
    expect(screen.queryByText("0.0000 mg")).not.toBeInTheDocument();
  });

  it("separates plan status from facts and records a planned dose with one action", async () => {
    let recorded = false;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (url.includes("plan-comparison")) {
        const slot = recorded ? plannedSlot({
          actual_amount: "10.0000",
          actual_local_time: "2026-08-09T11:30:00",
          dose_id: "33333333-3333-4333-8333-333333333333",
          status: "late",
          minutes_from_scheduled: 270,
        }) : plannedSlot();
        return Promise.resolve(jsonResponse(comparison({
          regimen_version_id: "44444444-4444-4444-8444-444444444444",
          regimen_version_label: "Synthetic approved regimen",
          regimen_versions: [{ id: "44444444-4444-4444-8444-444444444444", version_label: "Synthetic approved regimen", effective_from: "2026-01-01T00:00:00", effective_to: null }],
          slots: [slot],
          planned_total: "10.0000",
          actual_total: recorded ? "10.0000" : "0.0000",
          missed_slots: recorded ? 0 : 1,
        })));
      }
      if (url.includes("stress-episodes")) {
        return Promise.resolve(jsonResponse([{
          id: "55555555-5555-4555-8555-555555555555",
          trigger: "Synthetic illness",
          severity: "mild",
          status: "open",
          started_at: "2026-08-09T08:00:00Z",
          ended_at: null,
          timezone: "America/New_York",
          highest_temperature_c: null,
          illness_description: null,
          notes: null,
          recovery_notes: null,
          outcome: null,
          dose_count: 0,
          symptom_count: 1,
        }]));
      }
      if (url.endsWith("/api/v1/doses") && init?.method === "POST") {
        recorded = true;
        return Promise.resolve(jsonResponse({ id: "33333333-3333-4333-8333-333333333333" }, 201));
      }
      return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
    });

    renderToday();

    expect(await screen.findByText("Not recorded")).toBeVisible();
    expect(screen.getByText("No dose record exists for this slot. “Not recorded” does not mean “not taken.”")).toBeVisible();
    expect(screen.getByText("Trigger recorded:")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Record 10 mg taken now" }));

    await waitFor(() => { expect(screen.getByText("Recorded late")).toBeVisible(); });
    const write = fetchMock.mock.calls.find(([input, init]) => requestUrl(input).endsWith("/api/v1/doses") && init?.method === "POST");
    expect(write).toBeDefined();
    expect(new Headers(write?.[1]?.headers).get("X-CSRF-Token")).toBe("synthetic-csrf");
    const requestBody = write?.[1]?.body;
    expect(typeof requestBody).toBe("string");
    expect(JSON.parse(typeof requestBody === "string" ? requestBody : "{}") as unknown).toEqual(expect.objectContaining({
      medication_id: "22222222-2222-4222-8222-222222222222",
      amount: "10.0000",
      unit: "mg",
      route: "oral",
      category: "scheduled",
      slot_id: "11111111-1111-4111-8111-111111111111",
      time: expect.objectContaining({ timezone: "America/New_York" }),
    }));
  });

  it("shows both historical plan periods when the approved plan changes during the day", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.includes("plan-comparison")) return Promise.resolve(jsonResponse(comparison({
        regimen_versions: [
          { id: "44444444-4444-4444-8444-444444444444", version_label: "Morning plan", effective_from: "2026-01-01T00:00:00", effective_to: "2026-08-09T12:00:00" },
          { id: "66666666-6666-4666-8666-666666666666", version_label: "Afternoon plan", effective_from: "2026-08-09T12:00:00", effective_to: null },
        ],
        slots: [plannedSlot(), plannedSlot({ slot_id: "77777777-7777-4777-8777-777777777777", scheduled_local_time: "17:00:00" })],
        planned_total: "20.0000",
        missed_slots: 2,
      })));
      if (url.includes("stress-episodes")) return Promise.resolve(jsonResponse([]));
      return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
    });

    renderToday();

    expect(await screen.findByRole("heading", { name: "2 approved plan periods" })).toBeVisible();
    expect(screen.getByText(/physician-approved plan changed during this day/)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "No approved plan for this date" })).not.toBeInTheDocument();
  });
});
