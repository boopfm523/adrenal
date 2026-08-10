import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(await screen.findByText("Synthetic medicine at 07:00:00: 10.0000 mg -> 12.0000 mg")).toBeVisible();
    expect(screen.getByText("No removed schedule entries.")).toBeVisible();
  });

  it("deletes only a draft after password and exact high-friction confirmation", async () => {
    const draft = version("33333333-3333-4333-8333-333333333333", "Disposable synthetic draft", "draft", "2026-09-01T00:00:00");
    const approved = version("22222222-2222-4222-8222-222222222222", "Approved synthetic plan", "approved", "2026-08-01T00:00:00");
    const retired = version("11111111-1111-4111-8111-111111111111", "Retired synthetic plan", "retired", "2026-01-01T00:00:00");
    const requests: { url: string; method: string; body: unknown }[] = [];
    const submittedPassword = ["synthetic", "password"].join("-");
    let removed = false;
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
    expect(screen.getAllByText("Delete this unapproved draft")).toHaveLength(1);
    await userEvent.click(screen.getByText("Delete this unapproved draft"));
    const password = screen.getByLabelText("Current password");
    const confirmation = screen.getByLabelText("Type DELETE DRAFT PLAN");
    const submit = screen.getByRole("button", { name: "Permanently delete draft" });
    await userEvent.tab();
    expect(password).toHaveFocus();
    await userEvent.type(password, submittedPassword);
    await userEvent.tab();
    expect(confirmation).toHaveFocus();
    await userEvent.type(confirmation, "DELETE DRAFT PLAN");
    await userEvent.tab();
    expect(submit).toHaveFocus();
    await userEvent.click(submit);

    expect(await screen.findByRole("status")).toHaveTextContent("unapproved draft was permanently deleted");
    expect(screen.queryByRole("heading", { name: "Disposable synthetic draft" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Approved synthetic plan" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Retired synthetic plan" })).toBeVisible();
    const deletion = requests.find((request) => request.method === "DELETE");
    expect(deletion).toEqual({
      url: `/api/v1/regimens/${draft.id}`,
      method: "DELETE",
      body: { password: submittedPassword, confirmation: "DELETE DRAFT PLAN" },
    });
  });
});
