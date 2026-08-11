import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { PlanPage } from "./PlanPage";

function version(id: string, label: string, status: "draft" | "approved" | "retired", effective: string) {
  return {
    id, category: "plan", version_label: label, status, effective_from: effective, effective_to: null,
    approved_at: status === "approved" ? "2026-08-01T14:00:00Z" : null,
    approved_by: status === "approved" ? "Dr Synthetic" : null,
    approval_source: status === "approved" ? "Synthetic clinic letter" : null,
    retired_at: status === "retired" ? "2026-07-01T00:00:00Z" : null,
    notes: null,
    deletion_allowed: true,
    slots: [{ id: `${id.slice(0, 8)}-aaaa-4aaa-8aaa-aaaaaaaaaaaa`, medication_id: "99999999-9999-4999-8999-999999999999", medication_name: "Synthetic medicine", scheduled_local_time: "07:00:00", amount: "10.0000", unit: "mg", route: "oral", condition: null, sort_order: 0, category: "plan" }],
    instructions: [],
  };
}

function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }
function response(body: unknown): Response { return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }); }

describe("Medication plan page", () => {
  it("keeps approval provenance visible, distinguishes drafts, and renders a deterministic diff", async () => {
    const approved = version("22222222-2222-4222-8222-222222222222", "Approved synthetic plan", "approved", "2026-08-01T00:00:00");
    const retired = version("11111111-1111-4111-8111-111111111111", "Retired synthetic plan", "retired", "2026-01-01T00:00:00");
    const draft = version("33333333-3333-4333-8333-333333333333", "Future synthetic draft", "draft", "2026-09-01T00:00:00");
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(approved));
      if (url.includes("/diff/")) return Promise.resolve(response({ added: ["Synthetic medicine 12.0000 mg at 07:00:00"], removed: [], changed: ["Synthetic medicine at 07:00:00: 10.0000 mg -> 12.0000 mg"] }));
      if (url.endsWith("/regimens")) return Promise.resolve(response([draft, approved, retired]));
      return Promise.resolve(response({ detail: "not found" }));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><PlanPage /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "Approved synthetic plan · currently in force" })).toBeVisible();
    expect(screen.getAllByText("Dr Synthetic").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Synthetic clinic letter").length).toBeGreaterThan(0);
    expect(screen.getAllByText("2026-08-01T14:00:00Z").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Draft plan—not physician approved").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Draft plan—not physician approved. This version is not in force.").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Next step: review and record physician approval" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Record physician approval" })).toBeVisible();
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
      if (url.endsWith("/regimens")) return Promise.resolve(response(removed ? [approved, retired] : [draft, approved, retired]));
      if (url.includes("/diff/")) return Promise.resolve(response({ added: [], removed: [], changed: [] }));
      return Promise.resolve(response({ detail: "not found" }));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><PlanPage /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "Disposable synthetic draft" })).toBeVisible();
    const deleteButtons = screen.getAllByRole("button", { name: "Delete plan" });
    expect(deleteButtons).toHaveLength(3);
    const draftDelete = deleteButtons[0];
    if (draftDelete === undefined) throw new Error("draft delete button is missing");
    await userEvent.click(draftDelete);

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Disposable synthetic draft"));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Recorded doses stay in HealthCurve"));
    expect(await screen.findByRole("status")).toHaveTextContent("selected development plan was permanently deleted");
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
      if (url.endsWith("/regimens")) return Promise.resolve(response([draft]));
      return Promise.resolve(response({ detail: "not found" }));
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(<QueryClientProvider client={queryClient}><MemoryRouter><PlanPage /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "Synthetic plan" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Delete plan" })).not.toBeInTheDocument();
    view.unmount();

    const developmentDraft = { ...draft, deletion_allowed: true };
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (init?.method === "DELETE") requests.push(url);
      if (url.endsWith("/regimens/active")) return Promise.resolve(response(null));
      if (url.endsWith("/regimens")) return Promise.resolve(response([developmentDraft]));
      return Promise.resolve(response({ detail: "not found" }));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><PlanPage /></MemoryRouter></QueryClientProvider>);
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
      if (url.endsWith("/regimens")) return Promise.resolve(response(createdDraft === null ? [] : [createdDraft]));
      return Promise.resolve(response({ added: [], removed: [], changed: [] }));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><PlanPage /></MemoryRouter></QueryClientProvider>);

    await userEvent.click(await screen.findByRole("button", { name: "Create first plan draft" }));
    expect(screen.getByRole("heading", { name: "Create your first plan draft" })).toHaveFocus();
    await userEvent.type(screen.getByLabelText("Version label"), "My real plan");
    await userEvent.type(screen.getByLabelText("Effective from"), "2026-08-15T07:00");
    const medicationSelect = screen.getByLabelText("Medication and formulation");
    expect(within(medicationSelect).getByRole("option", { name: "Synthetic medicine tablet — formulation strength: 10 mg" })).toBeInTheDocument();
    medicationSelect.focus();
    await userEvent.tab();
    expect(screen.getByLabelText("Time")).toHaveFocus();
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

    expect(await screen.findByRole("status")).toHaveTextContent("Next, review it below and record physician approval");
    expect(screen.getByRole("heading", { name: "Next step: review and record physician approval" })).toHaveFocus();
    expect(screen.getByText(/Required provenance: the approving clinician or role and the source of approval/i)).toBeVisible();
    expect(screen.getByText(/A future-dated plan will wait until that time/i)).toBeVisible();
    const creation = requests.find((request) => request.method === "POST" && request.url.endsWith("/regimens"));
    expect(creation?.body).toMatchObject({
      version_label: "My real plan",
      effective_from: "2026-08-15T07:00",
      slots: [{ medication_id: medication.id, amount: "5", unit: "mg", route: "oral" }],
      instructions: [],
    });
    expect(requests.some((request) => request.url.includes("/approve"))).toBe(false);
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
      if (url.endsWith("/regimens")) return Promise.resolve(response([]));
      if (url.endsWith("/medications") && method === "POST") {
        medications = [createdMedication];
        return Promise.resolve(response(createdMedication));
      }
      if (url.endsWith("/medications")) return Promise.resolve(response(medications));
      return Promise.resolve(response({ detail: "not found" }));
    });
    const view = render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><PlanPage /></MemoryRouter></QueryClientProvider>);

    await userEvent.click(await screen.findByRole("button", { name: "Create first plan draft" }));
    expect(view.container.querySelector("form form")).toBeNull();
    expect(view.container.querySelectorAll("form")).toHaveLength(2);
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
      if (url.endsWith("/regimens")) return Promise.resolve(response([isApproved ? approved : draft]));
      return Promise.resolve(response({ detail: "not found" }));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><PlanPage /></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByRole("heading", { name: "Next step: review and record physician approval" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Record physician approval" }));
    expect(requests.some((request) => request.url.endsWith(`/regimens/${draft.id}/approve`) && request.method === "POST")).toBe(false);
    await userEvent.type(screen.getByLabelText("Approving clinician or role"), "Dr Synthetic");
    await userEvent.type(screen.getByLabelText("Approval source"), "Portal message");
    await userEvent.click(screen.getByLabelText(/confirm this records a real clinician-approved plan/i));
    await userEvent.click(screen.getByRole("button", { name: "Record physician approval" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Check provenance and overlapping effective dates");
    await userEvent.click(screen.getByRole("button", { name: "Record physician approval" }));

    expect(await screen.findByRole("status")).toHaveTextContent("applies this plan according to its effective dates");
    expect(await screen.findByRole("heading", { name: "Reviewed synthetic draft · currently in force" })).toBeVisible();
    expect(screen.getAllByText("Dr Synthetic").length).toBeGreaterThan(0);
    const approval = requests.filter((request) => request.url.endsWith(`/regimens/${draft.id}/approve`) && request.method === "POST").at(-1);
    expect(approval?.body).toEqual({ approved_by: "Dr Synthetic", approval_source: "Portal message", approved_at: null, source_document_checksum: null });
  });
});
