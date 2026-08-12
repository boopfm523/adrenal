import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { sessionStore } from "../api/session";
import { DayAnalysisCard } from "./DayAnalysisCard";

const analysis = {
  category: "ai" as const,
  id: "11111111-1111-4111-8111-111111111111",
  analysis_type: "daily_summary" as const,
  body: "- Stress rose near the recorded symptom. [sources: synthetic]",
  selected_date: "2026-08-11",
  timezone: "America/New_York",
  source_revision_sha256: "a".repeat(64),
  source_record_count: 3,
  generated_at: "2026-08-12T01:00:00Z",
  model_name: "qwen3:30b",
  model_digest: "sha256:synthetic",
  prompt_version: "healthcurve-day-analysis-v1",
  schema_version: "analysis-v1",
  disclaimer: "Generated analysis.",
  stale: false,
};

function renderCard(): void {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><DayAnalysisCard day="2026-08-11" timezone="America/New_York" /></QueryClientProvider>);
}

describe("day analysis card", () => {
  beforeEach(() => {
    sessionStore.set({ csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } });
  });

  afterEach(() => { sessionStore.clear(); });

  it("generates a labeled interpretation and displays reproducibility provenance", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => Promise.resolve(new Response(JSON.stringify(init?.method === "POST" ? { outcome: "created", detail: null, analysis } : null), { headers: { "Content-Type": "application/json" } })));
    renderCard();

    expect(await screen.findByText(/No AI interpretation has been saved/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Analyze this day" }));

    expect(await screen.findByText(/Stress rose near the recorded symptom/)).toBeVisible();
    expect(screen.getByText(/Current for this source revision/)).toBeVisible();
    fireEvent.click(screen.getByText("AI analysis provenance"));
    expect(screen.getByText("healthcurve-day-analysis-v1 / analysis-v1")).toBeVisible();
  });

  it("marks a saved result stale and handles a missing private model safely", async () => {
    const stale = { ...analysis, stale: true };
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => Promise.resolve(new Response(JSON.stringify(init?.method === "POST" ? { outcome: "model_unavailable", detail: "The configured private model is unavailable. Recorded facts and the HealthCurve remain available.", analysis: null } : stale), { headers: { "Content-Type": "application/json" } })));
    renderCard();

    expect(await screen.findByText(/Recorded data changed after this analysis/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Analyze this day again" }));
    expect(await screen.findByText(/configured private model is unavailable/)).toBeVisible();
    await waitFor(() => { expect(screen.getByText(/Recorded data changed after this analysis/)).toBeVisible(); });
  });
});
