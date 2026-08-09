import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { DataQualityPage } from "./DataQualityPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage(): void {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><DataQualityPage /></MemoryRouter></QueryClientProvider>);
}

describe("Data quality page", () => {
  it("separates actionable problems from genuine source absences and links both", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({
      completeness_notice: "No known findings does not mean the health record is clinically complete.",
      findings: [
        { id: "job:synthetic", finding_kind: "problem", severity: "high", source: "job queue", title: "Import stopped after retries", detail: "A synthetic import requires review.", record_id: "11111111-1111-4111-8111-111111111111", href: "/data-quality#operations", action_label: "Review failed operation" },
        { id: "garmin:synthetic:hrv", finding_kind: "genuine_absence", severity: "information", source: "Garmin", title: "HRV was not supplied", detail: "The provider did not supply HRV; no zero value was inferred.", record_id: "22222222-2222-4222-8222-222222222222", href: "/settings#integration-heading", action_label: "Review Garmin connection" },
      ],
    }));
    renderPage();

    expect(screen.getByRole("status")).toHaveTextContent("Checking data quality");
    expect(await screen.findByRole("heading", { name: "Problems to review" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Known genuine absences" })).toBeVisible();
    expect(screen.getByText(/not a recorded value of zero/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Review failed operation" })).toHaveAttribute("href", "/data-quality#operations");
    expect(screen.getByRole("link", { name: "Review Garmin connection" })).toHaveAttribute("href", "/settings#integration-heading");
  });

  it("shows a bounded empty state instead of claiming completeness", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ completeness_notice: "No known findings does not mean the health record is clinically complete.", findings: [] }));
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
