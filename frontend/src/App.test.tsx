import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

import { App } from "./App";
import { AuthContext, type AuthContextValue } from "./auth/context";

const auth: AuthContextValue = {
  status: "authenticated",
  session: { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: "Synthetic Owner", defaultTimezone: "America/New_York" } },
  signIn: vi.fn(),
  signOut: vi.fn(),
};

function LocationProbe(): React.JSX.Element {
  return <output aria-label="Current route">{useLocation().pathname}</output>;
}

function renderApp(entry: string): void {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter initialEntries={[entry]}><App /><LocationProbe /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);
}

describe("primary HealthCurve route", () => {
  beforeEach(() => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ detail: "synthetic unavailable" }), { status: 503, headers: { "Content-Type": "application/json" } }));
  });

  it.each(["/", "/analytics"])("routes %s to the daily HealthCurve", async (entry) => {
    renderApp(entry);
    await waitFor(() => { expect(screen.getByLabelText("Current route")).toHaveTextContent("/healthcurve"); });
    expect(await screen.findByRole("heading", { name: "Daily review", level: 1 })).toBeVisible();
    const brands = screen.getAllByRole("link", { name: "HealthCurve.ai home" });
    expect(brands).toHaveLength(2);
    expect(brands.every((brand) => brand.getAttribute("aria-current") === "page")).toBe(true);
    expect(brands[0]?.querySelector("img")).toHaveAttribute(
      "src",
      expect.stringContaining("healthcurve-protective-horizon-concept"),
    );
    expect(brands[0]?.querySelector("img")).toHaveAttribute("alt", "");
    expect(document.title).toBe("HealthCurve.ai");
    cleanup();
  });

  it.each([
    ["/healthcurve", "Daily review"],
    ["/today", "Today"],
    ["/timeline", "Timeline"],
    ["/doses", "Doses"],
    ["/symptoms-diary", "Symptoms & diary"],
    ["/plan", "Medication plan"],
    ["/episodes", "Stress episodes"],
    ["/settings", "Settings & privacy"],
    ["/data-quality", "Data quality"],
    ["/reports", "Reports"],
    ["/help", "Help"],
    ["/health-data", "Health data"],
    ["/labs", "Laboratory results"],
  ])("supports authenticated direct navigation to %s", async (entry, heading) => {
    renderApp(entry);
    expect(await screen.findByRole("heading", { name: heading, level: 1 })).toBeVisible();
    expect(screen.getByLabelText("Current route")).toHaveTextContent(entry);
    cleanup();
  });
});
