import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AuthContext } from "../auth/context";
import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { PlanPage } from "./PlanPage";

const auth = { status: "authenticated" as const, session: { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } }, signIn: vi.fn(), signOut: vi.fn() };
function renderPage(initialEntry = "/plan", client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) { return render(<HealthCurveProvider><QueryClientProvider client={client}><MemoryRouter initialEntries={[initialEntry]}><AuthContext.Provider value={auth}><PlanPage /></AuthContext.Provider></MemoryRouter></QueryClientProvider></HealthCurveProvider>); }

function version(id: string, label: string, status: "draft" | "approved" | "retired", effective: string | null, effectiveTo: string | null = null) {
  return {
    id, category: "plan", version_label: label, status, effective_from: effective, effective_to: effectiveTo,
    effective_timezone: "America/New_York",
    effective_from_local: effective,
    effective_to_local: effectiveTo,
    effective_from_utc_offset_minutes: -240,
    effective_to_utc_offset_minutes: effectiveTo === null ? null : -240,
    effective_time_provenance: "explicit_timezone",
    approved_at: status === "approved" ? "2026-08-01T14:00:00Z" : null,
    approved_by: status === "approved" ? "Dr Synthetic" : null,
    approval_source: status === "approved" ? "Synthetic clinic letter" : null,
    retired_at: status === "retired" ? "2026-07-01T00:00:00Z" : null,
    notes: null,
    deletion_allowed: true,
    slots: [{ id: `${id.slice(0, 8)}-aaaa-4aaa-8aaa-aaaaaaaaaaaa`, medication_id: "99999999-9999-4999-8999-999999999999", medication_name: "Synthetic medicine", timing_mode: "fixed_time", scheduled_local_time: "07:00:00", reminder_local_time: null, amount: "10.0000", unit: "mg", route: "oral", condition: null, sort_order: 0, category: "plan" }],
    instructions: [],
  };
}

function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
function response(body: unknown): Response { return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }); }
function versionPage(items: unknown[]): Record<string, unknown> { return { items, page: { page: 1, page_size: 25, total_items: items.length, total_pages: 1 } }; }

describe("Medication plan page", () => {
  it("sorts medication and formulation choices alphabetically after the placeholder", async () => {
    const medications = [
      { id: "30000000-0000-4000-8000-000000000000", category: "plan", name: "Zeta medicine", formulation: "tablet", strength: "5", strength_unit: "mg", default_unit: "mg", default_route: "oral", active_from: null, active_to: null, notes: null },
      { id: "20000000-0000-4000-8000-000000000000", category: "plan", name: "alpha medicine", formulation: "tablet", strength: "20", strength_unit: "mg", default_unit: "mg", default_route: "oral", active_from: null, active_to: null, notes: null },
      { id: "10000000-0000-4000-8000-000000000000", category: "plan", name: "Alpha medicine", formulation: "tablet", strength: "5", strength_unit: "mg", default_unit: "mg", default_route: "oral", active_from: null, active_to: null, notes: null },
      { id: "40000000-0000-4000-8000-000000000000", category: "plan", name: "Beta medicine", formulation: "injection", strength: "50", strength_unit: "mg", default_unit: "mg", default_route: "intravenous", active_from: null, active_to: null, notes: null },
    ];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([])));
      if (url.endsWith("/medications")) return Promise.resolve(response(medications));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Create first plan draft" }));
    const options = within(screen.getByLabelText("Medication and formulation")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Choose medication and formulation",
      "Alpha medicine tablet — formulation strength: 5 mg",
      "alpha medicine tablet — formulation strength: 20 mg",
      "Beta medicine injection — formulation strength: 50 mg",
      "Zeta medicine tablet — formulation strength: 5 mg",
    ]);
  });

  it("shows an accessible empty plan timeline", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([])));
      if (url.endsWith("/medications")) return Promise.resolve(response([]));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Plan timeline" })).toBeVisible();
    expect(screen.getByText("No plan versions to place on the timeline.")).toBeVisible();
  });

  it("shows adjacent and open-ended periods without inventing an overlap", async () => {
    const first = version("11111111-1111-4111-8111-111111111111", "First synthetic plan", "retired", "2026-08-01T07:00:00", "2026-08-02T07:00:00");
    const second = version("22222222-2222-4222-8222-222222222222", "Ongoing synthetic plan", "approved", "2026-08-02T07:00:00");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(second));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([second, first])));
      if (url.includes("/diff/")) return Promise.resolve(response({ added: [], removed: [], changed: [] }));
      if (url.endsWith("/medications")) return Promise.resolve(response([]));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    await screen.findAllByText("Ongoing synthetic plan");
    const timeline = (await screen.findByRole("heading", { name: "Plan timeline" })).closest("section");
    expect(timeline).not.toBeNull();
    if (timeline === null) throw new Error("plan timeline is missing");
    expect(within(timeline).getByText((_, element) => element?.tagName === "SPAN" && element.textContent === "Aug 2, 2026, 7:00 AM (America/New_York, UTC−04:00) through ongoing")).toBeVisible();
    expect(within(timeline).queryByRole("heading", { name: "Overlapping effective periods" })).not.toBeInTheDocument();
  });

  it("discloses legacy effective times whose original timezone is unknown", async () => {
    const legacy = {
      ...version("44444444-4444-4444-8444-444444444444", "Legacy synthetic plan", "retired", "2026-07-01T07:00:00"),
      effective_timezone: null,
      effective_from_local: null,
      effective_to_local: null,
      effective_from_utc_offset_minutes: null,
      effective_to_utc_offset_minutes: null,
      effective_time_provenance: "legacy_naive_utc_ambiguous",
    };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([legacy])));
      if (url.endsWith("/medications")) return Promise.resolve(response([]));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    expect((await screen.findAllByText("(legacy time; original timezone unknown)")).length).toBeGreaterThan(0);
  });

  it("distinguishes draft overlaps from overlaps between approved historical periods", async () => {
    const retired = version("11111111-1111-4111-8111-111111111111", "Retired synthetic plan", "retired", "2026-08-01T07:00:00", "2026-08-10T07:00:00");
    const approved = version("22222222-2222-4222-8222-222222222222", "Approved synthetic plan", "approved", "2026-08-05T07:00:00", "2026-08-20T07:00:00");
    const draft = version("33333333-3333-4333-8333-333333333333", "Draft synthetic plan", "draft", "2026-08-15T07:00:00");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(approved));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([draft, approved, retired])));
      if (url.includes("/diff/")) return Promise.resolve(response({ added: [], removed: [], changed: [] }));
      if (url.endsWith("/medications")) return Promise.resolve(response([]));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    expect(await screen.findByText("Approved-plan overlap:")).toBeVisible();
    expect(screen.getByText("Draft overlap:")).toBeVisible();
    expect(screen.getByText(/can end one currently live predecessor/i)).toBeVisible();
  });

  it("loads shareable effective-date filters and keeps them while paging", async () => {
    const urls: string[] = [];
    const row = version("33333333-3333-4333-8333-333333333333", "Filtered synthetic plan", "retired", "2026-08-09T00:00:00");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      urls.push(url);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.includes("/regimens?")) {
        const second = url.includes("page=2");
        return Promise.resolve(response({ items: [row], page: { page: second ? 2 : 1, page_size: 25, total_items: 26, total_pages: 2 } }));
      }
      if (url.includes("/diff/")) return Promise.resolve(response({ added: [], removed: [], changed: [] }));
      return Promise.resolve(response([]));
    });
    renderPage("/plan?local_date_from=2026-08-09&local_date_to=2026-08-10&timezone=America%2FNew_York");

    expect(await screen.findByRole("heading", { name: "Filtered synthetic plan" })).toBeVisible();
    expect(urls.some((url) => url.includes("local_date_from=2026-08-09") && url.includes("local_date_to=2026-08-10") && url.includes("timezone=America%2FNew_York"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => { expect(urls.some((url) => url.includes("page=2") && url.includes("local_date_from=2026-08-09"))).toBe(true); });
  });

  it("rejects a reversed effective-date range before loading history", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => { const url = requestUrl(input); urls.push(url); if (url.endsWith("/regimens/active")) return Promise.resolve(response(null)); return Promise.resolve(response([])); });
    renderPage("/plan?local_date_from=2026-08-11&local_date_to=2026-08-10&timezone=America%2FNew_York");
    expect(await screen.findByRole("alert")).toHaveTextContent("From date must be on or before Through date");
    expect(urls.some((url) => url.includes("/regimens?"))).toBe(false);
  });

  it("keeps approval provenance visible, distinguishes drafts, and renders a deterministic diff", async () => {
    const approved = {
      ...version("22222222-2222-4222-8222-222222222222", "Approved synthetic plan", "approved", "2026-08-01T00:00:00"),
      instructions: [{
        id: "77777777-7777-4777-8777-777777777777",
        category: "plan",
        instruction_category: "daily",
        title: "Synthetic clinician instruction",
        body: "Synthetic approved instruction body.",
        authored_by: "Dr Synthetic",
        authored_on: "2026-08-01",
        sort_order: 0,
      }],
    };
    const retired = version("11111111-1111-4111-8111-111111111111", "Retired synthetic plan", "retired", "2026-01-01T00:00:00");
    const draft = version("33333333-3333-4333-8333-333333333333", "Future synthetic draft", "draft", "2026-09-01T00:00:00");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(approved));
      if (url.includes("/diff/")) return Promise.resolve(response({ added: ["Synthetic medicine 12.0000 mg at 07:00:00"], removed: [], changed: ["Synthetic medicine at 07:00:00: 10.0000 mg -> 12.0000 mg"] }));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([draft, approved, retired])));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Approved synthetic plan · currently in force" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Medication plan version history table" })).toBeVisible();
    expect(screen.getAllByText("Dr Synthetic").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Synthetic clinic letter").length).toBeGreaterThan(0);
    const instructionSummary = screen.getAllByText("Physician-authored instructions")[0];
    expect(instructionSummary).toBeDefined();
    const instructionDisclosure = instructionSummary?.closest("details") ?? null;
    expect(instructionDisclosure).not.toHaveAttribute("open");
    if (instructionDisclosure === null) throw new Error("physician instruction disclosure is missing");
    expect(within(instructionDisclosure).getByText("Synthetic approved instruction body.")).not.toBeVisible();
    if (instructionSummary === undefined) throw new Error("physician instruction disclosure is missing");
    await userEvent.click(instructionSummary);
    expect(instructionDisclosure).toHaveAttribute("open");
    expect(within(instructionDisclosure).getByText("Synthetic approved instruction body.")).toBeVisible();
    expect(screen.getAllByText("Aug 1, 2026, 10:00 AM EDT").length).toBeGreaterThan(0);
    const effectiveStart = screen.getAllByText("Aug 1, 2026, 12:00 AM")[0];
    expect(effectiveStart?.closest("dd")).toHaveTextContent("Aug 1, 2026, 12:00 AM (America/New_York, UTC−04:00) through ongoing");
    expect(screen.queryByText("2026-08-01T14:00:00Z")).not.toBeInTheDocument();
    expect(document.querySelector('time[datetime="2026-08-01T14:00:00Z"]')).not.toBeNull();
    expect(screen.getAllByText("Draft plan—not physician approved").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Draft plan—not physician approved. This version is not in force.").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Next step: review and set this plan live" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Set physician-approved plan live" })).toBeVisible();
    expect(await screen.findByText("Synthetic medicine at 07:00:00: 10 mg -> 12 mg")).toBeVisible();
    expect(screen.getByText("No removed schedule entries.")).toBeVisible();
  });

  it("deletes any development plan after one ordinary confirmation", async () => {
    const draft = version("33333333-3333-4333-8333-333333333333", "Disposable synthetic draft", "draft", "2026-09-01T00:00:00");
    const approved = version("22222222-2222-4222-8222-222222222222", "Approved synthetic plan", "approved", "2026-08-01T00:00:00");
    const retired = version("11111111-1111-4111-8111-111111111111", "Retired synthetic plan", "retired", "2026-01-01T00:00:00");
    const requests: { url: string; method: string; body: unknown }[] = [];
    let removed = false;
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as unknown : undefined;
      requests.push({ url, method, body });
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(approved));
      if (url.endsWith(`/regimens/${draft.id}`) && method === "DELETE") {
        removed = true;
        return Promise.resolve(new Response(null, { status: 204 }));
      }
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage(removed ? [approved, retired] : [draft, approved, retired])));
      if (url.includes("/diff/")) return Promise.resolve(response({ added: [], removed: [], changed: [] }));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Disposable synthetic draft" })).toBeVisible();
    const deleteButtons = screen.getAllByRole("button", { name: "Delete plan" });
    expect(deleteButtons).toHaveLength(3);
    const draftDelete = deleteButtons[0];
    if (draftDelete === undefined) throw new Error("draft delete button is missing");
    await userEvent.click(draftDelete);

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Disposable synthetic draft"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Recorded doses stay in HealthCurve"));
    expect(await screen.findByText(/selected development plan was permanently deleted/)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Disposable synthetic draft" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Approved synthetic plan" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Retired synthetic plan" })).toBeVisible();
    const deletion = requests.find((request) => request.method === "DELETE");
    expect(deletion).toEqual({
      url: `/api/v1/regimens/${draft.id}`,
      method: "DELETE",
      body: undefined,
    });
  });

  it("does nothing when plan deletion is cancelled and hides deletion outside development", async () => {
    const draft = { ...version("33333333-3333-4333-8333-333333333333", "Synthetic plan", "draft", "2026-09-01T00:00:00"), deletion_allowed: false };
    const requests: string[] = [];
    vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (init?.method === "DELETE") requests.push(url);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([draft])));
      return Promise.resolve(response({ detail: "not found" }));
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = renderPage("/plan", queryClient);

    expect(await screen.findByRole("heading", { name: "Synthetic plan" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Delete plan" })).not.toBeInTheDocument();
    view.unmount();

    const developmentDraft = { ...draft, deletion_allowed: true };
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (init?.method === "DELETE") requests.push(url);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([developmentDraft])));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Delete plan" }));
    expect(requests).toHaveLength(0);
  });

  it("creates an unapproved draft from the guided form without recording approval", async () => {
    const medication = {
      id: "99999999-9999-4999-8999-999999999999", category: "plan", name: "Synthetic medicine",
      formulation: "tablet", strength: "10", strength_unit: "mg", default_unit: "mg", default_route: "oral",
      active_from: null, active_to: null, notes: null,
    };
    const requests: { url: string; method: string; body: Record<string, unknown> | undefined }[] = [];
    let createdDraft: ReturnType<typeof version> | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
      requests.push({ url, method, body });
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.endsWith("/medications")) return Promise.resolve(response([medication]));
      if (url.endsWith("/regimens") && method === "POST") {
        createdDraft = version("33333333-3333-4333-8333-333333333333", "My real plan", "draft", "2026-08-15T07:00:00");
        return Promise.resolve(response(createdDraft));
      }
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage(createdDraft === null ? [] : [createdDraft])));
      return Promise.resolve(response({ added: [], removed: [], changed: [] }));
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Create first plan draft" }));
    expect(screen.getByRole("heading", { name: "Create your first plan draft" })).toHaveFocus();
    await userEvent.type(screen.getByLabelText("Version label"), "My real plan");
    await userEvent.type(screen.getByLabelText("Effective start (optional)"), "2026-08-15T07:00");
    expect(screen.getByRole("heading", { name: "Proposed effective interval" })).toBeVisible();
    expect(screen.getByText("No overlap with the plan versions shown in history.")).toBeVisible();
    expect(screen.getByText(/Leave the start blank to use the exact moment you set this plan live/i)).toBeVisible();
    const medicationSelect = screen.getByLabelText("Medication and formulation");
    expect(within(medicationSelect).getByRole("option", { name: "Synthetic medicine tablet — formulation strength: 10 mg" })).toBeInTheDocument();
    medicationSelect.focus();
    await userEvent.tab();
    expect(screen.getByLabelText("Timing")).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByLabelText("Scheduled time")).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByLabelText("Scheduled dose amount")).toHaveFocus();
    await userEvent.selectOptions(medicationSelect, medication.id);
    const scheduledAmount = screen.getByLabelText("Scheduled dose amount");
    expect(scheduledAmount).toHaveAccessibleDescription(/may differ from the formulation strength/i);
    await userEvent.clear(scheduledAmount);
    await userEvent.type(scheduledAmount, "0");
    expect(scheduledAmount).toBeInvalid();
    await userEvent.click(screen.getByRole("button", { name: "Save unapproved draft" }));
    expect(requests.some((request) => request.method === "POST" && request.url.endsWith("/regimens"))).toBe(false);
    await userEvent.clear(scheduledAmount);
    await userEvent.type(scheduledAmount, "5");
    expect(scheduledAmount).toBeValid();
    await userEvent.click(screen.getByRole("button", { name: "Save unapproved draft" }));

    expect(await screen.findByText(/Next, review it below and set it live/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Next step: review and set this plan live" })).toHaveFocus();
    expect(screen.getByText(/Required provenance: the approving clinician or role and the source of approval/i)).toBeVisible();
    expect(screen.getByRole("heading", { name: "What will happen" })).toBeVisible();
    const creation = requests.find((request) => request.method === "POST" && request.url.endsWith("/regimens"));
    expect(creation?.body).toMatchObject({
      version_label: "My real plan",
      effective_from: "2026-08-15T07:00",
      slots: [{ medication_id: medication.id, amount: "5", unit: "mg", route: "oral" }],
      instructions: [],
    });
    expect(requests.some((request) => request.url.includes("/approve"))).toBe(false);
  });

  it("saves wake timing with a reminder fallback and no invented scheduled time", async () => {
    const medication = {
      id: "99999999-9999-4999-8999-999999999999", category: "plan", name: "Synthetic medicine",
      formulation: "tablet", strength: "10", strength_unit: "mg", default_unit: "mg", default_route: "oral",
      active_from: null, active_to: null, notes: null,
    };
    const requests: { url: string; method: string; body: Record<string, unknown> | undefined }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
      requests.push({ url, method, body });
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.endsWith("/medications")) return Promise.resolve(response([medication]));
      if (url.endsWith("/regimens") && method === "POST") return Promise.resolve(response(version("33333333-3333-4333-8333-333333333333", "Wake plan", "draft", null)));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([])));
      return Promise.resolve(response({ added: [], removed: [], changed: [] }));
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Create first plan draft" }));
    await userEvent.type(screen.getByLabelText("Version label"), "Wake plan");
    await userEvent.selectOptions(screen.getByLabelText("Medication and formulation"), medication.id);
    await userEvent.selectOptions(screen.getByLabelText("Timing"), "wake");
    expect(screen.queryByLabelText("Scheduled time")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Reminder if unrecorded by")).toHaveValue("07:30");
    await userEvent.type(screen.getByLabelText("Scheduled dose amount"), "10");
    await userEvent.click(screen.getByRole("button", { name: "Save unapproved draft" }));

    const creation = requests.find((request) => request.method === "POST" && request.url.endsWith("/regimens"));
    expect(creation?.body).toMatchObject({
      slots: [{
        timing_mode: "wake",
        scheduled_local_time: null,
        reminder_local_time: "07:30",
      }],
    });
  });

  it("saves a draft without dates and previews activation-time handoff", async () => {
    const current = version("22222222-2222-4222-8222-222222222222", "Current synthetic plan", "approved", "2026-08-01T00:00:00");
    const undated = version("33333333-3333-4333-8333-333333333333", "Undated synthetic draft", "draft", null);
    const medication = {
      id: "99999999-9999-4999-8999-999999999999", category: "plan", name: "Synthetic medicine",
      formulation: "tablet", strength: "10", strength_unit: "mg", default_unit: "mg", default_route: "oral",
      active_from: null, active_to: null, notes: null,
    };
    const requests: { url: string; method: string; body: Record<string, unknown> | undefined }[] = [];
    let created = false;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
      requests.push({ url, method, body });
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(current));
      if (url.endsWith("/medications")) return Promise.resolve(response([medication]));
      if (url.endsWith("/regimens") && method === "POST") { created = true; return Promise.resolve(response(undated)); }
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage(created ? [undated, current] : [current])));
      if (url.includes("/diff/")) return Promise.resolve(response({ added: [], removed: [], changed: [] }));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Create new version from active plan" }));
    expect(screen.getByLabelText("Effective start (optional)")).toHaveValue("");
    expect(screen.getByLabelText("Effective through (optional)")).toHaveValue("");
    await userEvent.clear(screen.getByLabelText("Version label"));
    await userEvent.type(screen.getByLabelText("Version label"), "Undated synthetic draft");
    expect(screen.getByText(/start will be the moment you set this draft live/i)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Save unapproved draft" }));

    const creation = requests.find((request) => request.method === "POST" && request.url.endsWith("/regimens"));
    expect(creation?.body).toMatchObject({ effective_from: null, effective_to: null });
    expect(await screen.findByText(/automatically end “Current synthetic plan” at the new plan’s start/)).toBeVisible();
    expect(screen.getAllByText(/Starts when this draft is set live/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Leave blank to start now/)).toBeVisible();
  });

  it("does not recursively lengthen a copied plan label past the API limit", async () => {
    const current = version(
      "22222222-2222-4222-8222-222222222222",
      "2026 replacement schedule — new version1 — new version",
      "approved",
      "2026-08-12T00:00:00",
    );
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(current));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([current])));
      if (url.endsWith("/medications")) return Promise.resolve(response([]));
      return Promise.resolve(response({ added: [], removed: [], changed: [] }));
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Create new version from active plan" }));
    const label = screen.getByLabelText("Version label");
    expect(label).toHaveValue("2026 replacement schedule — new version");
    expect((label as HTMLInputElement).value.length).toBeLessThanOrEqual(60);
  });

  it("creates a medication in an independent, keyboard-operable form", async () => {
    const createdMedication = {
      id: "88888888-8888-4888-8888-888888888888", category: "plan", name: "Synthetic independent medicine",
      formulation: "tablet", strength: "5", strength_unit: "mg", default_unit: "mg", default_route: "oral",
      active_from: null, active_to: null, notes: null,
    };
    const requests: { url: string; method: string; body: Record<string, unknown> | undefined }[] = [];
    let medications: typeof createdMedication[] = [];
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
      requests.push({ url, method, body });
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([])));
      if (url.endsWith("/medications") && method === "POST") {
        medications = [createdMedication];
        return Promise.resolve(response(createdMedication));
      }
      if (url.endsWith("/medications")) return Promise.resolve(response(medications));
      return Promise.resolve(response({ detail: "not found" }));
    });
    const view = renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Create first plan draft" }));
    expect(view.container.querySelector("form form")).toBeNull();
    expect(view.container.querySelectorAll("form")).toHaveLength(3);
    await userEvent.click(screen.getByText("Add a medication to the list"));
    const name = screen.getByLabelText("Name");
    name.focus();
    await userEvent.keyboard("Synthetic independent medicine");
    await userEvent.tab();
    expect(screen.getByLabelText("Formulation")).toHaveFocus();
    await userEvent.keyboard("tablet");
    await userEvent.tab();
    await userEvent.keyboard("5");
    await userEvent.tab();
    await userEvent.keyboard("mg");
    screen.getByRole("button", { name: "Save medication" }).focus();
    await userEvent.keyboard("[Enter]");

    await waitFor(() => { expect(screen.getByLabelText("Medication and formulation")).toHaveValue(createdMedication.id); });
    expect(requests.filter((request) => request.method === "POST" && request.url.endsWith("/medications"))).toHaveLength(1);
    expect(requests.some((request) => request.method === "POST" && request.url.endsWith("/regimens"))).toBe(false);
    expect(consoleError.mock.calls.flat().join(" ")).not.toMatch(/form cannot be a descendant of form|nested form/i);
  });

  it("requires explicit provenance and acknowledgement before approving a draft", async () => {
    const draft = version("33333333-3333-4333-8333-333333333333", "Reviewed synthetic draft", "draft", "2026-09-01T00:00:00");
    const approved = { ...draft, status: "approved" as const, approved_at: "2026-08-11T12:00:00Z", approved_by: "Dr Synthetic", approval_source: "Portal message" };
    const requests: { url: string; method: string; body: Record<string, unknown> | undefined }[] = [];
    let approvalAttempts = 0;
    let isApproved = false;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as Record<string, unknown> : undefined;
      requests.push({ url, method, body });
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(isApproved ? approved : null));
      if (url.endsWith("/medications")) return Promise.resolve(response([]));
      if (url.endsWith(`/regimens/${draft.id}/approve`) && method === "POST") {
        approvalAttempts += 1;
        if (approvalAttempts === 1) return Promise.resolve(new Response(JSON.stringify({ detail: "overlapping effective dates" }), { status: 409, headers: { "Content-Type": "application/json" } }));
        isApproved = true;
        return Promise.resolve(response(approved));
      }
      if (url.includes("/regimens?")) return Promise.resolve(response(versionPage([isApproved ? approved : draft])));
      return Promise.resolve(response({ detail: "not found" }));
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: "Next step: review and set this plan live" })).toBeVisible();
    expect(screen.getByText(/The saved draft start is shown. Change it here if needed/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Set physician-approved plan live" }));
    expect(requests.some((request) => request.url.endsWith(`/regimens/${draft.id}/approve`) && request.method === "POST")).toBe(false);
    await userEvent.type(screen.getByLabelText("Approving clinician or role"), "Dr Synthetic");
    await userEvent.type(screen.getByLabelText("Approval source"), "Portal message");
    await userEvent.click(screen.getByLabelText(/confirm this records a real clinician-approved plan/i));
    await userEvent.click(screen.getByRole("button", { name: "Set physician-approved plan live" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("overlapping effective dates");
    await userEvent.click(screen.getByRole("button", { name: "Set physician-approved plan live" }));

    expect(await screen.findByText(/Plan set live with physician approval recorded/)).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Reviewed synthetic draft · currently in force" })).toBeVisible();
    expect(screen.getAllByText("Dr Synthetic").length).toBeGreaterThan(0);
    const approval = requests.filter((request) => request.url.endsWith(`/regimens/${draft.id}/approve`) && request.method === "POST").at(-1);
    expect(approval?.body).toEqual({
      approved_by: "Dr Synthetic",
      approval_source: "Portal message",
      approved_at: null,
      source_document_checksum: null,
      activation_local_time: "2026-09-01T00:00",
      activation_timezone: "America/New_York",
      activation_fold: null,
    });
  });
});
