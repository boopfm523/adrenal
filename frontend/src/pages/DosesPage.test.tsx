import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { AuthContext } from "../auth/context";
import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { DosesPage } from "./DosesPage";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } };

function renderPage(initialEntry = "/doses"): void {
  render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter initialEntries={[initialEntry]}><AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}><DosesPage /></AuthContext.Provider></MemoryRouter></QueryClientProvider></HealthCurveProvider>);
}

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

function page(items: unknown[], revisions: unknown[] = [], currentPage = 1, totalItems = items.length, totalPages = 1) {
  return { items, revisions, page: { page: currentPage, page_size: 25, total_items: totalItems, total_pages: totalPages } };
}

function supportingResponse(url: string): Response | null {
  if (url.endsWith("/medications")) return new Response(JSON.stringify([{ id: "33333333-3333-4333-8333-333333333333", category: "plan", name: "Synthetic medicine", formulation: "tablet", strength: null, strength_unit: null, default_unit: "mg", default_route: "oral", active_from: null, active_to: null, notes: null }]), { headers: { "Content-Type": "application/json" } });
  if (url.includes("/stress-episodes?") && url.includes("status_filter=open")) return new Response(JSON.stringify({ items: [{ id: "55555555-5555-4555-8555-555555555555", category: "fact", trigger: "Synthetic illness", status: "open", severity: null, started_at: "2026-08-09T08:00:00Z", ended_at: null, timezone: "America/New_York", highest_temperature_c: null, illness_description: null, recovery_notes: null, outcome: null, notes: null, dose_count: 0, symptom_count: 0 }], page: { page: 1, page_size: 25, total_items: 1, total_pages: 1 } }), { headers: { "Content-Type": "application/json" } });
  return null;
}

describe("Doses page", () => {
  afterEach(() => { sessionStore.clear(); });

  it("lists only the current fact, exposes history, and submits an exact correction", async () => {
    sessionStore.set(session);
    const original = dose("11111111-1111-4111-8111-111111111111", "10.0000", null, null);
    const current = dose("22222222-2222-4222-8222-222222222222", "10.1250", original.id, "Synthetic first correction");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const supporting = supportingResponse(url);
      if (supporting !== null) return Promise.resolve(supporting);
      if (url.endsWith(`/doses/${current.id}/correct`) && init?.method === "POST") {
        return Promise.resolve(new Response(JSON.stringify(dose("44444444-4444-4444-8444-444444444444", "10.2500", current.id, "Synthetic second correction")), { status: 201, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify(page([current], [original])), { headers: { "Content-Type": "application/json" } }));
    });
    renderPage("/doses?local_date_from=2026-08-09&local_date_to=2026-08-10&timezone=America%2FNew_York");

    const table = await screen.findByRole("table", { name: /current recorded dose facts ordered by experienced time/i });
    expect(within(table).getByRole("rowheader", { name: /Synthetic medicine\s*10\.125 mg/ })).toBeVisible();
    expect(within(table).queryByRole("rowheader", { name: /10\.0000 mg/ })).not.toBeInTheDocument();
    const region = screen.getByRole("region", { name: "Recorded doses table" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("local_date_from=2026-08-09") && requestUrl(input).includes("local_date_to=2026-08-10") && requestUrl(input).includes("timezone=America%2FNew_York"))).toBe(true);
    await userEvent.click(screen.getByText("Revision history (1)"));
    expect(screen.getByText(/10 mg/)).toBeVisible();

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
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => Promise.resolve(supportingResponse(requestUrl(input)) ?? new Response(JSON.stringify(page([later, equalFirst, equalSecond])), { headers: { "Content-Type": "application/json" } })));
    renderPage();

    const rows = within(await screen.findByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((row) => within(row).getByRole("rowheader").textContent)).toEqual([
      "Later medicine3 mg",
      "Equal first1 mg",
      "Equal second2 mg",
    ]);
  });

  it("shows a safe empty state without a table", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => Promise.resolve(supportingResponse(requestUrl(input)) ?? new Response(JSON.stringify(page([])), { headers: { "Content-Type": "application/json" } })));
    renderPage();

    expect(await screen.findByRole("heading", { name: "No doses recorded" })).toBeVisible();
    expect(screen.getByText("A missing record is not a recorded zero dose.")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("requests the next bounded page through accessible controls", async () => {
    const first = dose("11111111-1111-4111-8111-111111111111", "1.0000", null, null);
    const last = dose("22222222-2222-4222-8222-222222222222", "2.0000", null, null);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const supporting = supportingResponse(requestUrl(input));
      if (supporting !== null) return Promise.resolve(supporting);
      const secondPage = requestUrl(input).includes("page=2");
      return Promise.resolve(new Response(JSON.stringify(secondPage ? page([last], [], 2, 26, 2) : page([first], [], 1, 26, 2)), { headers: { "Content-Type": "application/json" } }));
    });
    renderPage("/doses?local_date_from=2026-08-01&timezone=America%2FNew_York");

    expect(await screen.findByText("Showing 1–25 of 26. Page 1 of 2.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => { expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("/doses?") && requestUrl(input).includes("page=2") && requestUrl(input).includes("local_date_from=2026-08-01"))).toBe(true); });
    expect(await screen.findByText("Showing 26–26 of 26. Page 2 of 2.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("records regular by default and stress only when explicitly selected", async () => {
    sessionStore.set(session);
    const writes: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const supporting = supportingResponse(url);
      if (supporting !== null) return Promise.resolve(supporting);
      if (url.endsWith("/doses") && init?.method === "POST") {
        writes.push(JSON.parse(typeof init.body === "string" ? init.body : "{}") as unknown);
        return Promise.resolve(new Response(JSON.stringify(dose("77777777-7777-4777-8777-777777777777", "5.0000", null, null)), { status: 201, headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify(page([])), { headers: { "Content-Type": "application/json" } }));
    });
    renderPage();

    const form = await screen.findByRole("form", { name: "Record a dose taken" });
    expect(within(form).getByLabelText(/^Dose type/)).toHaveValue("scheduled");
    expect(within(form).getByText(/Linking an episode adds context but does not change/)).toBeVisible();
    await waitFor(() => { expect(within(form).getByRole("option", { name: /Synthetic medicine/ })).toBeVisible(); });
    await userEvent.selectOptions(within(form).getByLabelText("Medication"), "33333333-3333-4333-8333-333333333333");
    await userEvent.type(within(form).getByLabelText("Amount"), "5");
    await userEvent.selectOptions(within(form).getByLabelText(/^Dose type/), "stress");
    await userEvent.selectOptions(within(form).getByLabelText(/^Related episode/), "55555555-5555-4555-8555-555555555555");
    await userEvent.click(within(form).getByRole("button", { name: "Record dose taken" }));
    await waitFor(() => { expect(writes).toHaveLength(1); });
    expect(writes[0]).toEqual(expect.objectContaining({ category: "stress", episode_id: "55555555-5555-4555-8555-555555555555" }));
  });
});
