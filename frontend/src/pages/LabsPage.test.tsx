import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { AuthContext } from "../auth/context";
import { LabsPage } from "./LabsPage";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } };
const documentId = "55555555-5555-4555-8555-555555555555";
const base = { id: "11111111-1111-4111-8111-111111111111", panel_id: "22222222-2222-4222-8222-222222222222", source_document_id: null, source_page_number: null, category: "fact", analyte_name: "Cortisol AM", original_value: "10", qualitative_result: null, original_unit: "mcg/dL", original_reference_range: "source range", abnormal_flag: null, normalized_analyte_code: "cortisol", normalized_analyte_name: "Cortisol", normalized_value: "276.0000000000", normalized_unit: "nmol/L", normalization_method: "hc-lab-normalization-v1:cortisol:27.6", specimen_time: { occurred_at: "2026-08-09T12:00:00Z", local_time: "2026-08-09T08:00:00", timezone: "America/New_York", utc_offset_minutes: -240 }, specimen_type: "Serum", laboratory_name: "Synthetic laboratory", source_type: "web", confirmation_state: "direct" };
const document = { document_id: documentId, display_name: "synthetic-results.pdf", media_type: "application/pdf", sha256: "a".repeat(64), byte_size: 1024, status: "stored", page_count: 1, rejection_reason: null, created_at: "2026-08-09T12:00:00Z", validated_at: "2026-08-09T12:01:00Z", extraction_status: "draft_ready", extraction_draft_id: "66666666-6666-4666-8666-666666666666", draft_state: "pending" };
const extraction = { category: "ai_draft", draft_id: document.extraction_draft_id, document_id: documentId, state: "pending", schema_version: "lab-pdf-v2", prompt_version: "deterministic-no-prompt-v1", model_name: null, candidates: [{ page_number: 1, row_index: 1, extraction_tier: "embedded_text", coordinate_space: "pdf_points", parsed: true, analyte_name: "Synthetic sodium", original_value: "140", original_unit: "mmol/L", original_reference_range: "135-145", source_text: "Synthetic sodium 140 mmol/L 135-145", confidence: 0.98, flags: [], requires_confirmation: true }, { page_number: 1, row_index: 2, extraction_tier: "embedded_text", coordinate_space: "pdf_points", parsed: false, analyte_name: null, original_value: null, original_unit: null, original_reference_range: null, source_text: "Synthetic unreadable note", confidence: 0.2, flags: ["unparsed_row"], requires_confirmation: true }] };
const deletionPreview = { document_id: documentId, mode: "confirmed_report", requires_password: true, confirmation_phrase: "DELETE CONFIRMED LAB REPORT 55555555", extraction_draft_ids: [document.extraction_draft_id], panel_ids: [base.panel_id], result_ids: [base.id], derived_result_count: 1, trend_point_count: 1, ai_analysis_ids: ["77777777-7777-4777-8777-777777777777"], report_snapshot_ids: ["88888888-8888-4888-8888-888888888888"], report_artifact_ids: ["99999999-9999-4999-8999-999999999999"], page_preview_count: 1, private_storage_artifact_count: 4, backups_may_retain_until_expiry: true };

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
function page(items: unknown[]): Record<string, unknown> { return { items, page: { page: 1, page_size: 25, total_items: items.length, total_pages: 1 } }; }

function renderPage(): void {
  sessionStore.set(session);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><MemoryRouter><AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}><LabsPage /></AuthContext.Provider></MemoryRouter></QueryClientProvider>);
}

describe("Labs page", () => {
  afterEach(() => { sessionStore.clear(); });

  it("separates source and derived data and provides an equivalent trend table", async () => {
    const results = [base, { ...base, id: "33333333-3333-4333-8333-333333333333", specimen_time: { ...base.specimen_time, occurred_at: "2026-08-10T12:00:00Z", local_time: "2026-08-10T08:00:00" }, original_value: "11", normalized_value: "303.6000000000" }, { ...base, id: "44444444-4444-4444-8444-444444444444", specimen_type: "Saliva", original_unit: "unsupported", normalized_value: null, normalized_unit: null, normalization_method: null }];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => Promise.resolve(json(requestUrl(input).includes("/labs/documents?") ? page([]) : page(results))));
    renderPage();
    expect(await screen.findByRole("heading", { name: "Cortisol — Serum" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Source result" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Derived result" })).toBeVisible();
    expect(screen.getByText("Not derived—original preserved")).toBeVisible();
    expect(screen.getByRole("region", { name: "Cortisol — Serum data table" })).toBeInTheDocument();
    expect(screen.getAllByText("276").length).toBeGreaterThan(0);
    expect(screen.getAllByText("303.6").length).toBeGreaterThan(0);
    expect(screen.queryByText(/276\.0000000000/)).not.toBeInTheDocument();
    expect(screen.getByText(/does not diagnose, interpret cortisol/)).toBeVisible();
  });

  it("links each confirmed PDF result back to its exact source page", async () => {
    const linked = { ...base, source_document_id: documentId, source_page_number: 3, source_type: "file_import", confirmation_state: "confirmed_from_draft" };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => Promise.resolve(json(requestUrl(input).includes("/labs/documents?") ? page([]) : page([linked]))));
    renderPage();
    const link = await screen.findByRole("link", { name: "View source page 3" });
    expect(link).toHaveAttribute("href", `/api/v1/labs/documents/${documentId}/pages/3/preview`);
    expect(screen.getAllByRole("link", { name: "Download original PDF" })).toEqual(
      expect.arrayContaining([expect.objectContaining({ href: expect.stringContaining(`/api/v1/labs/documents/${documentId}/download`) })]),
    );
  });

  it("shows source beside editable candidates and confirms only reviewed facts", async () => {
    let confirmationBody: unknown;
    let csrf: string | null = null;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (init?.method === "POST" && url.endsWith(`/labs/documents/${documentId}/confirm`)) {
        confirmationBody = JSON.parse(typeof init.body === "string" ? init.body : "{}") as unknown;
        csrf = new Headers(init.headers).get("X-CSRF-Token");
        return Promise.resolve(json({ category: "fact", panel_id: "77777777-7777-4777-8777-777777777777", created: true, result_count: 1 }));
      }
      if (url.endsWith(`/labs/documents/${documentId}/extraction`)) return Promise.resolve(json(extraction));
      if (url.endsWith(`/labs/documents/${documentId}`)) return Promise.resolve(json(document));
      if (url.includes("/labs/documents?")) return Promise.resolve(json(page([document])));
      return Promise.resolve(json(page([])));
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Open review" }));
    const form = await screen.findByRole("form", { name: "Confirm extracted lab results" });
    expect(within(form).getByText("embedded text extraction · confidence 98%")).toBeVisible();
    expect(screen.getByRole("img", { name: "Inert preview of source lab document synthetic-results.pdf, page 1" })).toHaveAttribute("src", `/api/v1/labs/documents/${documentId}/pages/1/preview`);
    const downloads = screen.getAllByRole("link", { name: "Download original PDF" });
    expect(downloads).toHaveLength(2);
    downloads.forEach((download) => { expect(download).toHaveAttribute("href", `/api/v1/labs/documents/${documentId}/download`); });
    expect(screen.getByText("Unparsed evidence requiring manual entry (1)")).toBeVisible();
    await userEvent.type(within(form).getByLabelText("Specimen collection local time"), "2026-08-09T08:00");
    await userEvent.type(within(form).getByLabelText("Report local time"), "2026-08-09T09:00");
    const analyte = within(form).getByLabelText("Analyte");
    await userEvent.clear(analyte);
    await userEvent.type(analyte, "Synthetic sodium corrected");
    await userEvent.click(within(form).getByRole("button", { name: "Confirm included rows as recorded facts" }));
    await waitFor(() => { expect(confirmationBody).toBeDefined(); });
    expect(csrf).toBe("synthetic-csrf");
    expect(confirmationBody).toEqual(expect.objectContaining({
      specimen_time: { local_time: "2026-08-09T08:00", timezone: "America/New_York" },
      candidates: [expect.objectContaining({ candidate_index: 0, included: true, analyte_name: "Synthetic sodium corrected", original_value: "140" })],
    }));
  });

  it("previews exact confirmed-report dependencies and sends reauthenticated deletion", async () => {
    const submittedPassword = ["synthetic", "password"].join("-");
    let deletionBody: unknown;
    let deletionCsrf: string | null = null;
    const confirmedDocument = { ...document, draft_state: "confirmed" };
    const linked = { ...base, source_document_id: documentId, source_page_number: 1, source_type: "file_import", confirmation_state: "confirmed_from_draft" };
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (init?.method === "DELETE" && url.endsWith(`/labs/documents/${documentId}`)) {
        deletionBody = JSON.parse(typeof init.body === "string" ? init.body : "{}") as unknown;
        deletionCsrf = new Headers(init.headers).get("X-CSRF-Token");
        return Promise.resolve(json({ status: "deletion_queued", document_id: documentId, cleanup_task_count: 2 }, 202));
      }
      if (url.endsWith(`/labs/documents/${documentId}/deletion-preview`)) return Promise.resolve(json(deletionPreview));
      if (url.includes("/labs/documents?")) return Promise.resolve(json(page([confirmedDocument])));
      return Promise.resolve(json(page([linked])));
    });
    renderPage();

    await userEvent.click(await screen.findByText("Review permanent deletion"));
    const impact = await screen.findByRole("region", { name: "Lab report deletion impact" });
    const resultRow = within(impact).getByRole("row", { name: "Recorded lab results 1" });
    expect(within(resultRow).getByRole("rowheader", { name: "Recorded lab results" })).toBeVisible();
    expect(within(resultRow).getByRole("cell", { name: "1" })).toBeVisible();
    expect(screen.getByText(/every listed snapshot and all of its rendered files/)).toBeVisible();
    const form = screen.getByRole("form", { name: "Delete lab report synthetic-results.pdf" });
    await userEvent.type(within(form).getByLabelText("Current password"), submittedPassword);
    await userEvent.type(
      within(form).getByLabelText(`Type ${deletionPreview.confirmation_phrase}`),
      deletionPreview.confirmation_phrase,
    );
    await userEvent.click(within(form).getByRole("button", { name: "Permanently delete confirmed report unit" }));

    await waitFor(() => { expect(deletionBody).toBeDefined(); });
    expect(deletionCsrf).toBe("synthetic-csrf");
    expect(deletionBody).toEqual({ password: submittedPassword, confirmation: deletionPreview.confirmation_phrase });
  });
});
