import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { DosesPage } from "./DosesPage";

function dose(id: string, amount: string, supersedesId: string | null, reason: string | null, occurredAt = "2026-08-09T11:00:00Z", medicationName = "Synthetic medicine") {
  return {
    id,
    category: "fact",
    medication_id: "33333333-3333-4333-8333-333333333333",
    medication_name: medicationName,
    amount,
    unit: "mg",
    route: "oral",
    dose_category: "scheduled",
    time: { occurred_at: occurredAt, local_time: occurredAt.replace("Z", ""), timezone: "UTC", utc_offset_minutes: 0 },
    provenance: { recorded_at: "2026-08-09T11:01:00Z", source_type: "web", confirmation_state: "direct", supersedes_id: supersedesId, correction_reason: reason, is_correction: supersedesId !== null },
    regimen_version_id: null,
    slot_id: null,
    episode_id: null,
    notes: null,
  };
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

describe("Doses page", () => {
  afterEach(() => { sessionStore.clear(); });

  it("lists only the current fact, exposes history, and submits an exact correction", async () => {
    sessionStore.set({ csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } });
    const original = dose("11111111-1111-4111-8111-111111111111", "10.0000", null, null);
    const current = dose("22222222-2222-4222-8222-222222222222", "10.1250", original.id, "Synthetic first correction");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (url.endsWith(`/doses/${current.id}/correct`) && init?.method === "POST") {
        return Promise.resolve(new Response(JSON.stringify(dose("44444444-4444-4444-8444-444444444444", "10.2500", current.id, "Synthetic second correction")), { status: 201, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify([current, original]), { headers: { "Content-Type": "application/json" } }));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter><DosesPage /></MemoryRouter></QueryClientProvider>);

    const table = await screen.findByRole("table", { name: /current recorded dose facts ordered by experienced time/i });
    expect(within(table).getByRole("rowheader", { name: /Synthetic medicine\s*10\.1250 mg/ })).toBeVisible();
    expect(within(table).queryByRole("rowheader", { name: /10\.0000 mg/ })).not.toBeInTheDocument();
    const region = screen.getByRole("region", { name: "Recorded doses table" });
    expect(region).toHaveAttribute("tabindex", "0");
    await userEvent.click(screen.getByText("Revision history (1)"));
    expect(screen.getByText(/10\.0000 mg/)).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Correct recorded fact" }));
    const form = screen.getByRole("form", { name: "Correct Synthetic medicine dose" });
    const amount = within(form).getByLabelText("Amount");
    await userEvent.clear(amount);
    await userEvent.type(amount, "10.2500");
    await userEvent.type(within(form).getByLabelText("Correction reason"), "Synthetic second correction");
    await userEvent.click(within(form).getByRole("button", { name: "Save corrected fact" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input, init]) => requestUrl(input).includes(`/doses/${current.id}/correct`) && init?.method === "POST")).toBe(true);
    });
    const write = fetchMock.mock.calls.find(([input, init]) => requestUrl(input).includes(`/doses/${current.id}/correct`) && init?.method === "POST");
    expect(new Headers(write?.[1]?.headers).get("X-CSRF-Token")).toBe("synthetic-csrf");
    const body = write?.[1]?.body;
    expect(JSON.parse(typeof body === "string" ? body : "{}") as unknown).toEqual({ reason: "Synthetic second correction", changes: { amount: "10.2500" } });
  });

  it("orders current facts by experienced time with deterministic equal-time IDs", async () => {
    const later = dose("33333333-3333-4333-8333-333333333333", "3.0000", null, null, "2026-08-09T12:00:00Z", "Later medicine");
    const equalSecond = dose("22222222-2222-4222-8222-222222222222", "2.0000", null, null, "2026-08-09T10:00:00Z", "Equal second");
    const equalFirst = dose("11111111-1111-4111-8111-111111111111", "1.0000", null, null, "2026-08-09T10:00:00Z", "Equal first");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify([later, equalSecond, equalFirst]), { headers: { "Content-Type": "application/json" } }));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><DosesPage /></MemoryRouter></QueryClientProvider>);

    const rows = within(await screen.findByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual([
      "Equal first1.0000 mg",
      "Equal second2.0000 mg",
      "Later medicine3.0000 mg",
    ]);
  });

  it("shows a safe empty state without a table", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("[]", { headers: { "Content-Type": "application/json" } }));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><DosesPage /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "No doses recorded" })).toBeVisible();
    expect(screen.getByText("A missing record is not a recorded zero dose.")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
