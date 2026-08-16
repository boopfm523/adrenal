import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { sessionStore } from "../api/session";
import { PatternAnalysisCard } from "./PatternAnalysisCard";

const analysis = {
  category: "ai" as const,
  id: "11111111-1111-4111-8111-111111111111",
  analysis_type: "pattern_observation" as const,
  body: "- The deterministic range has one cited pattern. [sources: synthetic]",
  source_record_ids: ["daily-feature:2026-08-01:synthetic"],
  computed_inputs: { total_days: 7 },
  range_start: "2026-08-01T04:00:00Z",
  range_end: "2026-08-08T04:00:00Z",
  generated_at: "2026-08-12T01:00:00Z",
  model_name: "qwen3:30b",
  model_digest: "sha256:synthetic",
  prompt_version: "analysis-v3",
  schema_version: "analysis-v1",
  disclaimer: "Generated analysis.",
};

function renderCard(): void {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><PatternAnalysisCard dateFrom="2026-08-01" dateTo="2026-08-07" timezone="America/New_York" /></QueryClientProvider>);
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}

describe("private Ollama pattern explanation", () => {
  beforeEach(() => {
    sessionStore.set({ csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York" } });
  });

  afterEach(() => {
    sessionStore.clear();
    vi.useRealTimers();
  });

  it("loads a saved completion after refresh and labels its local-only safety boundary", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse([analysis]));
    renderCard();

    expect(await screen.findByText(/one cited pattern/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Private Ollama pattern explanation" })).toBeVisible();
    expect(screen.getByText(/does not calculate the HealthCurve/)).toBeVisible();
    expect(screen.getByText(/No health text is sent to a cloud AI service/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Ask Ollama to refresh this draft" })).toBeEnabled();
  });

  it("keeps deterministic results available when the host model is unavailable and permits retry", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => Promise.resolve(jsonResponse(init?.method === "POST"
      ? { outcome: "model_unavailable", detail: "HealthCurve could not reach configured host Ollama. Deterministic results remain available.", analysis: null }
      : [])));
    renderCard();

    expect(await screen.findByText(/No completed private-model draft/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Ask Ollama to explain these patterns" }));
    expect(await screen.findByText(/could not reach configured host Ollama/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Try private analysis again" })).toBeEnabled();
  });

  it("announces elapsed time and lets the owner stop waiting without hiding deterministic data", async () => {
    vi.useFakeTimers();
    let posts = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      if (init?.method !== "POST") return Promise.resolve(jsonResponse([]));
      posts += 1;
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => { reject(new DOMException("Aborted", "AbortError")); });
      });
    });
    renderCard();

    await vi.waitFor(() => { expect(screen.getByText(/No completed private-model draft/)).toBeVisible(); });
    fireEvent.click(screen.getByRole("button", { name: "Ask Ollama to explain these patterns" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent(/2 seconds elapsed/);
    expect(screen.getByRole("button", { name: "Waiting for private Ollama…" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Waiting for private Ollama…" }));
    expect(posts).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Stop waiting" }));
    await vi.waitFor(() => { expect(screen.getByText(/This browser stopped waiting/)).toBeVisible(); });
    expect(screen.getByRole("button", { name: "Try private analysis again" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Check for a completed draft" })).toBeEnabled();
  });

  it("allows the slower local pattern model 135 seconds and successfully retries", async () => {
    vi.useFakeTimers();
    let posts = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      if (init?.method !== "POST") return Promise.resolve(jsonResponse([]));
      posts += 1;
      if (posts === 2) return Promise.resolve(jsonResponse({ outcome: "created", detail: null, analysis }));
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => { reject(new DOMException("Aborted", "AbortError")); });
      });
    });
    renderCard();

    await vi.waitFor(() => { expect(screen.getByText(/No completed private-model draft/)).toBeVisible(); });
    fireEvent.click(screen.getByRole("button", { name: "Ask Ollama to explain these patterns" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(135_000); });
    await vi.waitFor(() => { expect(screen.getByRole("alert")).toHaveTextContent(/stopped waiting after 135 seconds/); });
    vi.useRealTimers();
    fireEvent.click(screen.getByRole("button", { name: "Try private analysis again" }));
    expect(await screen.findByText(/one cited pattern/)).toBeVisible();
  });
});
