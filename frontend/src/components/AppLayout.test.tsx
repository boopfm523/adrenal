import { DEFAULT_THEME } from "@mantine/core";
import { describe, expect, it } from "vitest";

import { NAVIGATION_DRAWER_BREAKPOINT } from "./AppLayout";

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
});
