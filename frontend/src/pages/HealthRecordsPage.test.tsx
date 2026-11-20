import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { HealthRecordsPage } from "./HealthRecordsPage";

const recordId = "77777777-7777-4777-8777-777777777777";
const record = {
  category: "fact",
  id: recordId,
  dataset_table: "imaging_studies",
  record_id: "img_syn_001",
  title: "SYNTHETIC CT CHEST",
  category_label: "CT",
  kind: null,
  record_date: "2025-05-05",
  date_text: null,
  local_time: null,
  timezone: "America/Chicago",
  timezone_assumed: false,
  organization: "Mayo Clinic",
  source: "Johns Hopkins MyChart",
  state: "present",
  ai_category: "imaging_impressions",
  provider: null,
  ordered_by: "Dr. Synthetic Orderer",
  reading_provider: "Dr. Synthetic Radiologist",
  performing_lab: null,
  prescriber: null,
  summary_text: "Synthetic impression.",
  has_full_text: true,
  file_count: 1,
  revision: 2,
};
const detail = {
  ...record,
  body_text: "<script>alert('not markup')</script> Synthetic report.",
  file_paths: ["records/Mayo/Radiology/img_syn_001.html"],
  revisions: [
    { revision: 2, batch_id: "88888888-8888-4888-8888-888888888888", content_sha256: "b".repeat(64), created_at: "2025-06-02T12:00:00Z" },
    { revision: 1, batch_id: "99999999-9999-4999-8999-999999999999", content_sha256: "a".repeat(64), created_at: "2025-06-01T12:00:00Z" },
  ],
};
const withdrawn = { ...record, id: "12121212-1212-4212-8212-121212121212", record_id: "img_syn_002", title: "SYNTHETIC MRI", state: "no_longer_in_source", revision: 1 };

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}

function page(items: unknown[]): Record<string, unknown> {
  return { items, page: { page: 1, page_size: 25, total_items: items.length, total_pages: 1 } };
}

function mockApi(requests: string[]): void {
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = requestUrl(input);
    requests.push(url);
    if (url.includes("/health-records/batches")) return Promise.resolve(json(page([{ id: "88888888-8888-4888-8888-888888888888", dataset_schema_version: 2, dataset_generated_at: "2025-06-02T08:00:00", source_exported_at: { "Johns Hopkins MyChart": "2025-06-02" }, started_at: "2025-06-02T12:00:00Z", completed_at: "2025-06-02T12:01:00Z", counts: {} }])));
    if (url.endsWith("/health-records/organizations")) return Promise.resolve(json([{ key: "0123456789abcdef", name: "Mayo Clinic" }, { key: "fedcba9876543210", name: "NIH Clinical Center" }]));
    if (url.endsWith(`/health-records/${recordId}`)) return Promise.resolve(json(detail));
    return Promise.resolve(json(page([record, withdrawn])));
  });
}

function renderPage(entry = "/health-records"): void {
  render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[entry]}><HealthRecordsPage /></MemoryRouter></QueryClientProvider></HealthCurveProvider>);
}

describe("Health records page", () => {
  beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); });

  it("shows each record with the organization it came from and its status", async () => {
    const requests: string[] = [];
    mockApi(requests);
    renderPage();
    const table = await screen.findByRole("region", { name: "Imaging records" });
    expect(within(table).getAllByText("Mayo Clinic").length).toBe(2);
    expect(within(table).getByText("No longer in source")).toBeVisible();
    expect(within(table).getByText(/version 2/)).toBeVisible();
    expect(screen.getByText(/Hidden from chat and AI by default/)).toBeVisible();
    expect(screen.getByText(/Sources last pulled: Johns Hopkins MyChart on 2025-06-02/)).toBeVisible();
    expect(requests.some((url) => url.includes("dataset_table=imaging_studies"))).toBe(true);
  });

  it("renders imported text as plain text and offers originals only as downloads", async () => {
    mockApi([]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "SYNTHETIC CT CHEST" }));
    const heading = await screen.findByRole("heading", { name: "SYNTHETIC CT CHEST", level: 2 });
    expect(heading).toBeVisible();
    await waitFor(() => { expect(heading).toHaveFocus(); });
    expect(screen.getByText(/<script>alert\('not markup'\)<\/script> Synthetic report\./)).toBeVisible();
    expect(document.querySelector("script")).toBeNull();
    const download = screen.getByRole("link", { name: "Download img_syn_001.html" });
    expect(download).toHaveAttribute("href", `/api/v1/health-records/${recordId}/files/0`);
    expect(download).toHaveAttribute("download");
    expect(screen.getByText("Version 1, imported 2025-06-01 12:00 UTC")).toBeVisible();
  });

  it("labels medication lists as reference history that never reaches the plan or chat", async () => {
    const requests: string[] = [];
    mockApi(requests);
    renderPage("/health-records?table=medications");
    expect(await screen.findByText(/Medication lists as each organization recorded them/)).toBeVisible();
    expect(screen.getByText(/never shown to chat or AI/)).toBeVisible();
    expect(requests.some((url) => url.includes("dataset_table=medications"))).toBe(true);
  });
  it("joins category and kind cleanly and shortens long summaries in the list", async () => {
    const medication = { ...record, id: "34343434-3434-4434-8434-343434343434", dataset_table: "medications", title: "Synthetic tablet", category_label: null, kind: "Patient reported", ai_category: null, summary_text: "Take one daily. ".repeat(30) };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = requestUrl(input);
      if (url.includes("/health-records/batches")) return Promise.resolve(json(page([])));
      if (url.endsWith("/health-records/organizations")) return Promise.resolve(json([]));
      return Promise.resolve(json(page([medication])));
    });
    renderPage("/health-records?table=medications");
    const table = await screen.findByRole("region", { name: "Medication lists records" });
    expect(within(table).getByText("Patient reported")).toBeVisible();
    expect(within(table).queryByText(/·\s*Patient reported/)).not.toBeInTheDocument();
    expect(within(table).getByText(/… \(open the record for the rest\)$/)).toBeVisible();
  });
  it("filters by organization without putting its name in any URL", async () => {
    const requests: string[] = [];
    mockApi(requests);
    let location = "";
    function LocationProbe(): null { location = useLocation().search; return null; }
    render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={["/health-records"]}><HealthRecordsPage /><LocationProbe /></MemoryRouter></QueryClientProvider></HealthCurveProvider>);
    await screen.findByRole("option", { name: "Mayo Clinic" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "From" }), "Mayo Clinic");
    await userEvent.click(screen.getByRole("button", { name: "Show records" }));
    await waitFor(() => { expect(requests.some((url) => url.includes("organization_key=0123456789abcdef"))).toBe(true); });
    expect(location).not.toMatch(/mayo/iu);
    expect(requests.filter((url) => /mayo/iu.test(url))).toEqual([]);
  });
});
