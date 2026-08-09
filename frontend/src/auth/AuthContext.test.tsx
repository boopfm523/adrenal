import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { sessionStore } from "../api/session";
import { AuthProvider } from "./AuthContext";
import { ProtectedRoute } from "./ProtectedRoute";

function renderProtected(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/today"]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<h1>Sign in</h1>} />
            <Route element={<ProtectedRoute />}>
              <Route path="/today" element={<h1>Today</h1>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("central authentication", () => {
  beforeEach(() => {
    sessionStore.clear();
  });

  it("restores the cookie session and CSRF token through auth/me", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      csrf_token: "restored-csrf",
      email: "owner@example.test",
      display_name: null,
      default_timezone: "UTC",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));

    renderProtected();

    expect(await screen.findByRole("heading", { name: "Today" })).toBeVisible();
    expect(sessionStore.get()?.csrfToken).toBe("restored-csrf");
  });

  it("redirects an expired session to login", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ detail: "not authenticated" }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    ));

    renderProtected();

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeVisible();
  });
});
