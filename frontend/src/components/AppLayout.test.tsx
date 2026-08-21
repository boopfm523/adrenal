import { DEFAULT_THEME } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AuthContext, type AuthContextValue } from "../auth/context";
import { HealthCurveProvider } from "./HealthCurveProvider";
import {
  AppLayout,
  NAVIGATION_DRAWER_BREAKPOINT,
  NAVIGATION_DRAWER_MEDIA_QUERY,
} from "./AppLayout";

const auth: AuthContextValue = {
  status: "authenticated",
  session: {
    csrfToken: "synthetic-csrf-token",
    user: {
      email: "owner@example.test",
      displayName: "Synthetic Owner",
      defaultTimezone: "America/New_York",
    },
  },
  signIn: vi.fn(),
  signOut: vi.fn(),
};

function mockDrawerMedia(matches: boolean): void {
  vi.spyOn(window, "matchMedia").mockImplementation((query) => ({
    matches: query === NAVIGATION_DRAWER_MEDIA_QUERY ? matches : false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

function renderLayout(): void {
  render(
    <HealthCurveProvider>
      <AuthContext.Provider value={auth}>
        <MemoryRouter initialEntries={["/healthcurve"]}>
          <AppLayout />
        </MemoryRouter>
      </AuthContext.Provider>
    </HealthCurveProvider>,
  );
}

describe("AppLayout responsive navigation", () => {
  it("keeps representative iPhone and iPad widths in the shared hamburger mode", () => {
    const rootFontSize = 16;
    const drawerBreakpointPx = Number.parseFloat(
      DEFAULT_THEME.breakpoints[NAVIGATION_DRAWER_BREAKPOINT],
    ) * rootFontSize;

    expect(390).toBeLessThan(drawerBreakpointPx);
    expect(1024).toBeLessThan(drawerBreakpointPx);
    expect(drawerBreakpointPx).toBe(1200);
  });

  it("keeps the desktop sidebar persistent even when the browser window is narrow", () => {
    mockDrawerMedia(false);
    renderLayout();

    expect(screen.queryByRole("button", { name: "Open navigation" })).not.toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeVisible();
    expect(screen.getByRole("link", { name: "HealthCurve.ai home" })).toBeVisible();
    expect(screen.getByRole("link", { name: /Sick-day plan/ })).toHaveAttribute(
      "href",
      "/api/v1/private-documents/sick-day-plan",
    );
  });

  it("uses the hamburger drawer on touch-first phone and tablet layouts", () => {
    mockDrawerMedia(true);
    renderLayout();

    expect(screen.getByRole("button", { name: "Open navigation" })).toBeVisible();
    expect(screen.getAllByRole("link", { name: /Sick-day plan/ })).not.toHaveLength(0);
  });
});
