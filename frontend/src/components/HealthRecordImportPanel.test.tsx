import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { sessionStore } from "../api/session";
import { HealthCurveProvider } from "./HealthCurveProvider";
import { HealthRecordImportPanel } from "./HealthRecordImportPanel";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.com", displayName: null, defaultTimezone: "America/New_York", currentTimezone: "America/New_York" } };

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function run(dryRun: boolean, newLabs: number): Record<string, unknown> {
  return {
    dry_run: dryRun,
    batch_id: dryRun ? null : "88888888-8888-4888-8888-888888888888",
    dataset_generated_at: "2025-06-01T12:00:00",
    source_exported_at: {},
    tables: [
      { table: "lab_results", new: newLabs, changed: 0, unchanged: 7, no_longer_in_source: 1, returned: 0 },
      { table: "imaging_studies", new: 0, changed: 0, unchanged: 1, no_longer_in_source: 0, returned: 0 },
    ],
    lab_panels_created: dryRun ? 0 : 2,
    lab_panels_corrected: 0,
  };
}

function renderPanel(): void {
  sessionStore.set(session);
  render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><HealthRecordImportPanel /></QueryClientProvider></HealthCurveProvider>);
}

describe("Health-record import panel", () => {
  afterEach(() => { sessionStore.clear(); });

  it("checks for changes without writing, then imports, both with CSRF", async () => {
    const calls: { url: string; method: string; csrf: string | null }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      calls.push({ url, method: init?.method ?? "GET", csrf: new Headers(init?.headers).get("X-CSRF-Token") });
      const dryRun = url.includes("dry_run=true");
      return Promise.resolve(new Response(JSON.stringify(run(dryRun, 3)), { headers: { "Content-Type": "application/json" } }));
    });
    renderPanel();

    await userEvent.click(screen.getByRole("button", { name: "Check for changes" }));
    expect(await screen.findByText(/4 record change\(s\) would be imported\. Nothing was written\./)).toBeVisible();
    const preview = screen.getByRole("region", { name: "Changes an import would make" });
    expect(within(preview).getByRole("rowheader", { name: "Lab results" })).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Import now" }));
    expect(await screen.findByText(/Imported\. 2 lab panel\(s\) added and 0 corrected\./)).toBeVisible();

    expect(calls.map((call) => [call.method, call.url, call.csrf])).toEqual([
      ["POST", "/api/v1/health-records/imports?dry_run=true", "synthetic-csrf"],
      ["POST", "/api/v1/health-records/imports?dry_run=false", "synthetic-csrf"],
    ]);
  });

  it("shows the server's reason when the import is refused", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: "Import refused; nothing was written: dataset schema_version 3 is not supported" }), { status: 409, headers: { "Content-Type": "application/json" } }));
    renderPanel();
    await userEvent.click(screen.getByRole("button", { name: "Import now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/schema_version 3 is not supported/);
  });
});
