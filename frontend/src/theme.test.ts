import { DEFAULT_THEME } from "@mantine/core";

import { healthCurveCssVariables, healthCurveTheme, healthCurveTokens } from "./theme";

describe("HealthCurve Mantine theme", () => {
  it("provides branded and semantic design tokens from one source", () => {
    expect(healthCurveTheme.primaryColor).toBe("horizonTeal");
    expect(healthCurveTheme.colors?.protectiveNavy).toHaveLength(10);
    expect(healthCurveTheme.colors?.horizonTeal).toHaveLength(10);
    expect(healthCurveTheme.colors?.horizonOrange).toHaveLength(10);

    const variables = healthCurveCssVariables(DEFAULT_THEME);
    expect(variables.light["--hc-color-brand-navy"]).toBe(healthCurveTokens.brandNavy);
    expect(variables.light["--hc-color-success"]).toBe(healthCurveTokens.status.success);
    expect(variables.light["--hc-color-error"]).toBe(healthCurveTokens.status.error);
    expect(variables.variables["--hc-sidebar-width"]).toBe(healthCurveTokens.sidebar.width);
  });
});
