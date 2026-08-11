import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "./auth/context";
import { AppLayout } from "./components/AppLayout";
import { AccessibleLineChart } from "./components/AccessibleLineChart";
import { LoginPage } from "./pages/LoginPage";
import { PlanPage } from "./pages/PlanPage";

const auth: AuthContextValue = {
  status: "authenticated",
  session: { csrfToken: "synthetic-csrf-token", user: { email: "owner@example.test", displayName: "Synthetic Owner", defaultTimezone: "America/New_York" } },
  signIn: vi.fn(),
  signOut: vi.fn(),
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}

async function expectNoHighImpactViolations(): Promise<void> {
  // Vitest's synthetic document does not load index.html, which supplies these in production.
  document.documentElement.lang = "en";
  document.title = "HealthCurve.ai";
  const result = await axe.run(document, { resultTypes: ["violations"], rules: { "color-contrast": { enabled: false } } });
  const highImpact = result.violations.filter((violation) => violation.impact === "critical" || violation.impact === "serious");
  expect(highImpact.map((violation) => ({ id: violation.id, help: violation.help, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);
}

function renderRoute(element: React.JSX.Element): void {
  render(<AuthContext.Provider value={auth}><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={["/plan"]}><Routes><Route element={<AppLayout />}><Route path="/plan" element={element} /></Route></Routes></MemoryRouter></QueryClientProvider></AuthContext.Provider>);
}

function LocationProbe(): React.JSX.Element {
  return <h1>{useLocation().pathname}</h1>;
}

describe("automated accessibility audit", () => {
  it("has no serious or critical violations in the sign-in journey", async () => {
    render(<AuthContext.Provider value={{ ...auth, status: "anonymous", session: null }}><MemoryRouter><LoginPage /></MemoryRouter></AuthContext.Provider>);
    await expectNoHighImpactViolations();
  });

  it("audits the signed-in shell and physician-approval journey and keeps it keyboard reachable", async () => {
    const draft = {
      id: "33333333-3333-4333-8333-333333333333", category: "plan", version_label: "Synthetic keyboard plan", status: "draft",
      effective_from: "2099-01-01T07:00:00", effective_to: null, approved_at: null, approved_by: null, approval_source: null, retired_at: null, notes: null, deletion_allowed: false,
      slots: [], instructions: [],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.endsWith("/regimens/active")) return Promise.resolve(json(null));
      if (url.includes("/regimens?")) return Promise.resolve(json({ items: [draft], page: { page: 1, page_size: 25, total_items: 1, total_pages: 1 } }));
      if (url.endsWith("/medications")) return Promise.resolve(json([]));
      return Promise.resolve(json({ detail: "not found" }));
    });
    renderRoute(<PlanPage />);

    expect(await screen.findByRole("heading", { name: "Synthetic keyboard plan" })).toBeVisible();
    await expectNoHighImpactViolations();

    const user = userEvent.setup();
    const skipLink = screen.getByRole("link", { name: "Skip to main content" });
    skipLink.focus();
    expect(skipLink).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: "HealthCurve.ai home" })).toHaveFocus();
    const approvalButton = screen.getByRole("button", { name: "Record physician approval" });
    approvalButton.focus();
    await user.keyboard("[Enter]");
    const clinician = screen.getByLabelText("Approving clinician or role");
    expect(clinician).toBeInvalid();
    clinician.focus();
    await user.keyboard("Dr Synthetic");
    await user.tab();
    expect(screen.getByLabelText("Approval source")).toHaveFocus();
    await user.keyboard("Synthetic consultation");
    await user.tab();
    expect(screen.getByLabelText("Approval time (optional)")).toHaveFocus();
    await user.tab();
    expect(screen.getByLabelText(/confirm this records a real clinician-approved plan/i)).toHaveFocus();
    await user.keyboard("[Space]");
    await user.tab();
    expect(approvalButton).toHaveFocus();
  });

  it("activates every primary route by keyboard", async () => {
    render(<AuthContext.Provider value={auth}><MemoryRouter initialEntries={["/today"]}><Routes><Route element={<AppLayout />}><Route path="*" element={<LocationProbe />} /></Route></Routes></MemoryRouter></AuthContext.Provider>);
    const routes = [
      ["HealthCurve", "/healthcurve"], ["Today", "/today"], ["Timeline", "/timeline"], ["Doses", "/doses"], ["Plan", "/plan"], ["Episodes", "/episodes"],
      ["Symptoms & diary", "/symptoms-diary"], ["Health data", "/health-data"], ["Labs", "/labs"],
      ["Reports", "/reports"], ["Data quality", "/data-quality"], ["Settings & privacy", "/settings"], ["Help", "/help"],
    ] as const;
    const user = userEvent.setup();
    for (const [label, path] of routes) {
      const link = screen.getByRole("link", { name: label });
      link.focus();
      await user.keyboard("[Enter]");
      expect(await screen.findByRole("heading", { name: path })).toBeVisible();
      expect(link).toHaveAttribute("aria-current", "page");
    }
  });

  it("keeps an exact chart table keyboard-operable and free of high-impact violations", async () => {
    render(<AccessibleLineChart title="Synthetic accessibility chart" summary="Synthetic values with one explicit gap." unit="mg" timezone="America/New_York" dateRange="2026-08-01 through 2026-08-03" definition="Each point is synthetic." sampleCount={3} missingCount={1} series={[{ name: "Recorded facts", source: "synthetic fixture", values: [{ label: "2026-08-01", value: "5" }, { label: "2026-08-02", value: null }, { label: "2026-08-03", value: "7" }] }]} />);
    await expectNoHighImpactViolations();
    const user = userEvent.setup();
    const disclosure = screen.getByText("View data table");
    disclosure.focus();
    expect(disclosure.tagName).toBe("SUMMARY");
    expect(disclosure).toHaveFocus();
    await user.keyboard("[Enter]");
    // jsdom does not implement the native <summary> toggle default action.
    disclosure.closest("details")?.setAttribute("open", "");
    expect(screen.getByRole("region", { name: "Synthetic accessibility chart data table" })).toBeVisible();
    expect(screen.getByRole("cell", { name: "Gap—no value" })).toBeVisible();
  });
});
