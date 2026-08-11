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
    expect(screen.getByRole("heading", { name: "HealthCurve", level: 1 })).toBeVisible();
    expect(screen.getByRole("link", { name: "HealthCurve" })).toHaveAttribute("aria-current", "page");
    cleanup();
  });
});
