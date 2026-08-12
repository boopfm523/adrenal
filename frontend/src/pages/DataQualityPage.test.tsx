import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { DataQualityPage } from "./DataQualityPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage(initialEntry = "/data-quality"): void {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[initialEntry]}><DataQualityPage /></MemoryRouter></QueryClientProvider>);
}

describe("Data quality page", () => {
  it("separates actionable problems from genuine source absences and links both", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({
      completeness_notice: "No known findings does not mean the health record is clinically complete.",
      findings: [
        { id: "job:synthetic", finding_kind: "problem", severity: "high", source: "job queue", title: "Import stopped after retries", detail: "A synthetic import requires review.", record_id: "11111111-1111-4111-8111-111111111111", href: "/data-quality#operations", action_label: "Review failed operation" },
        { id: "open-episode:33333333-3333-4333-8333-333333333333", finding_kind: "problem", severity: "attention", source: "Stress episode", title: "Episode may still be open", detail: "Synthetic episode started Aug 10 at 09:00 EDT and has remained open for 2 days. HealthCurve has not inferred an end.", record_id: "33333333-3333-4333-8333-333333333333", href: "/episodes?history=all&review_episode=33333333-3333-4333-8333-333333333333#episode-33333333-3333-4333-8333-333333333333", action_label: "Review or close episode" },
        { id: "garmin:synthetic:hrv", finding_kind: "genuine_absence", severity: "information", source: "Garmin", title: "HRV was not supplied", detail: "The provider did not supply HRV; no zero value was inferred.", record_id: "22222222-2222-4222-8222-222222222222", href: "/settings#integration-heading", action_label: "Review Garmin connection" },
      ],
      page: { page: 1, page_size: 25, total_items: 3, total_pages: 1 },
    }));
    renderPage();

    expect(screen.getByRole("status")).toHaveTextContent("Checking data quality");
    expect(await screen.findByRole("heading", { name: "Problems to review" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Data-quality problems table" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Known genuine absences table" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Known genuine absences" })).toBeVisible();
    expect(screen.getByText(/not a recorded value of zero/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Review failed operation" })).toHaveAttribute("href", "/data-quality#operations");
    expect(screen.getByRole("link", { name: "Review or close episode" })).toHaveAttribute("href", "/episodes?history=all&review_episode=33333333-3333-4333-8333-333333333333#episode-33333333-3333-4333-8333-333333333333");
    expect(screen.getByText(/appear here after 24 hours/i)).toBeVisible();
    expect(screen.getByRole("link", { name: "Review Garmin connection" })).toHaveAttribute("href", "/settings#integration-heading");
  });

  it("keeps the bounded current-findings page in URL state without inventing dates", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => { const value = typeof input === "string" ? input : input instanceof URL ? input.href : input.url; urls.push(value); const second = value.includes("page=2"); return Promise.resolve(jsonResponse({ completeness_notice: "Synthetic completeness boundary.", findings: [], page: { page: second ? 2 : 1, page_size: 25, total_items: 26, total_pages: 2 } })); });
    renderPage("/data-quality?page=1");
    expect(await screen.findByText(/current derived review queue/i)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => { expect(urls.some((value) => value.includes("page=2"))).toBe(true); });
    expect(screen.queryByLabelText(/from date/i)).not.toBeInTheDocument();
  });

  it("explains and clears a reviewed Garmin sync notice without implying data deletion", async () => {
    const requests: { method: string; url: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? "GET";
      requests.push({ method, url });
      if (method === "POST") return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.resolve(jsonResponse({
        completeness_notice: "Synthetic completeness boundary.",
        findings: [{
          id: "garmin-sync:22222222-2222-4222-8222-222222222222",
          finding_kind: "problem",
          severity: "attention",
          source: "Garmin Connect · manual refresh",
          title: "Garmin sync completed with 3 data warnings",
          detail: "Request origin: Manual refresh. Completed for a synthetic window. This is a completed sync notice, not queued or running work.",
          record_id: "22222222-2222-4222-8222-222222222222",
          href: "/settings#garmin-connection",
          action_label: "Open Garmin sync settings",
          can_acknowledge: true,
        }],
        page: { page: 1, page_size: 25, total_items: 1, total_pages: 1 },
      }));
    });
    renderPage();

    expect(await screen.findByText(/not another queued run/i)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Clear reviewed notice" }));
    await waitFor(() => { expect(screen.queryByText("Garmin sync completed with 3 data warnings")).not.toBeInTheDocument(); });
    expect(screen.getByText(/without deleting sync history or health data/i)).toBeVisible();
    expect(requests).toContainEqual({
      method: "POST",
      url: "/api/v1/data-quality/garmin-syncs/22222222-2222-4222-8222-222222222222/acknowledge",
    });
  });

  it("shows a bounded empty state instead of claiming completeness", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ completeness_notice: "No known findings does not mean the health record is clinically complete.", findings: [], page: { page: 1, page_size: 25, total_items: 0, total_pages: 1 } }));
    renderPage();
    expect(await screen.findByRole("heading", { name: "No known data-quality findings" })).toBeVisible();
    expect(screen.getAllByText(/does not.*complete/i).length).toBeGreaterThan(0);
  });

  it("does not imply completeness when loading fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ detail: "synthetic failure" }, 503));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("No conclusion about record completeness can be made");
  });
});
