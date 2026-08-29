import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { DataQualityPage } from "./DataQualityPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage(initialEntry = "/data-quality"): void {
  render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={[initialEntry]}><DataQualityPage /></MemoryRouter></QueryClientProvider></HealthCurveProvider>);
}

describe("Data quality page", () => {
  it("separates actionable problems from genuine source absences and links both", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({
      completeness_notice: "No known findings does not mean the health record is clinically complete.",
      timezone: "America/New_York",
      findings: [
        { id: "job:synthetic", finding_kind: "problem", severity: "high", source: "job queue", title: "Import stopped after retries", detail: "A synthetic import requires review.", record_id: "11111111-1111-4111-8111-111111111111", href: "/data-quality#operations", action_label: "Review failed operation", can_acknowledge: false, acknowledge_label: null, occurred_at: "2026-08-12T17:30:00Z" },
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
    expect(screen.getByText("Aug 12, 2026, 1:30 PM EDT")).toBeVisible();
    expect(screen.getByText(/not a recorded value of zero/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Review failed operation" })).toHaveAttribute("href", "/data-quality#operations");
    expect(screen.getByRole("link", { name: "Review or close episode" })).toHaveAttribute("href", "/episodes?history=all&review_episode=33333333-3333-4333-8333-333333333333#episode-33333333-3333-4333-8333-333333333333");
    expect(screen.getByText(/appear here after 24 hours/i)).toBeVisible();
    expect(screen.getByRole("link", { name: "Review Garmin connection" })).toHaveAttribute("href", "/settings#integration-heading");
  });

  it("keeps the bounded current-findings page in URL state without inventing dates", async () => {
    const urls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => { const value = typeof input === "string" ? input : input instanceof URL ? input.href : input.url; urls.push(value); const second = value.includes("page=2"); return Promise.resolve(jsonResponse({ completeness_notice: "Synthetic completeness boundary.", timezone: "UTC", findings: [], page: { page: second ? 2 : 1, page_size: 25, total_items: 26, total_pages: 2 } })); });
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
        timezone: "America/New_York",
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
          acknowledge_label: "Clear reviewed notice",
          occurred_at: "2026-08-11T08:01:00Z",
        }],
        page: { page: 1, page_size: 25, total_items: 1, total_pages: 1 },
      }));
    });
    renderPage();

    expect(await screen.findByText(/completed sync notice/i)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Clear reviewed notice" }));
    await waitFor(() => { expect(screen.queryByText("Garmin sync completed with 3 data warnings")).not.toBeInTheDocument(); });
    expect(screen.getByText(/without deleting job history or health data/i)).toBeVisible();
    expect(requests).toContainEqual({
      method: "POST",
      url: "/api/v1/data-quality/garmin-syncs/22222222-2222-4222-8222-222222222222/acknowledge",
    });
  });

  it("removes the dead runbook action and clears all reviewed background failures", async () => {
    const requests: { method: string; url: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? "GET";
      requests.push({ method, url });
      if (method === "POST") return Promise.resolve(new Response(null, { status: 204 }));
      return Promise.resolve(jsonResponse({
        completeness_notice: "Synthetic completeness boundary.",
        timezone: "America/New_York",
        findings: [
          {
            id: "dead-letter:11111111-1111-4111-8111-111111111111",
            finding_kind: "problem",
            severity: "warning",
            source: "Background job queue",
            title: "Background task exhausted retries",
            detail: "Task ai.chat.respond; reason handler_failed. Clearing this notice keeps the job history and does not change health data.",
            record_id: "11111111-1111-4111-8111-111111111111",
            href: null,
            action_label: null,
            can_acknowledge: true,
            acknowledge_label: "Clear reviewed failure",
            occurred_at: "2026-08-17T13:24:57Z",
          },
          {
            id: "dead-letter:22222222-2222-4222-8222-222222222222",
            finding_kind: "problem",
            severity: "warning",
            source: "Background job queue",
            title: "Background task exhausted retries",
            detail: "Task telegram.dose_reminders.check; reason handler_failed. Clearing this notice keeps the job history and does not change health data.",
            record_id: "22222222-2222-4222-8222-222222222222",
            href: null,
            action_label: null,
            can_acknowledge: true,
            acknowledge_label: "Clear reviewed failure",
            occurred_at: "2026-08-12T17:47:32Z",
          },
        ],
        page: { page: 1, page_size: 25, total_items: 2, total_pages: 1 },
      }));
    });
    renderPage();

    expect(await screen.findByText("Aug 17, 2026, 9:24 AM EDT")).toBeVisible();
    expect(screen.queryByRole("link", { name: /operations runbook/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Clear reviewed failure" })).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Clear all reviewed failures" }));
    await waitFor(() => { expect(screen.queryByText("Background task exhausted retries")).not.toBeInTheDocument(); });
    expect(requests).toContainEqual({
      method: "POST",
      url: "/api/v1/data-quality/background-jobs/acknowledge-all",
    });
    expect(screen.getByText(/without deleting job history or health data/i)).toBeVisible();
  });

  it("shows a bounded empty state instead of claiming completeness", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ completeness_notice: "No known findings does not mean the health record is clinically complete.", timezone: "UTC", findings: [], page: { page: 1, page_size: 25, total_items: 0, total_pages: 1 } }));
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
