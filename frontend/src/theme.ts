import {
  Button,
  createTheme,
  type CSSVariablesResolver,
  type MantineColorsTuple,
} from "@mantine/core";

const protectiveNavy: MantineColorsTuple = [
  "#eef4fb",
  "#d9e7f5",
  "#b6d0e8",
  "#8db5d9",
  "#6d9dcb",
  "#588ec4",
  "#4b86c1",
  "#3973aa",
  "#2c6699",
  "#113f73",
];

const horizonTeal: MantineColorsTuple = [
  "#e7f9f5",
  "#d3eee8",
  "#a9dbd1",
  "#7bc7b8",
  "#57b6a4",
  "#3da997",
  "#2da38f",
  "#1d8e7c",
  "#117f6e",
  "#006f5f",
];

const horizonOrange: MantineColorsTuple = [
  "#fff4e6",
  "#ffe6ca",
  "#ffca91",
  "#ffab57",
  "#fd9128",
  "#f9810d",
  "#f67800",
  "#dc6600",
  "#c45a00",
  "#a94b00",
];

export const healthCurveTokens = {
  canvas: "#f7f8f5",
  surface: "#ffffff",
  surfaceMuted: "#eef3f0",
  text: "#17211d",
  textMuted: "#526059",
  border: "#c9d3cd",
  focus: "#f67800",
  status: {
    success: "#117f6e",
    warning: "#a94b00",
    error: "#9f2d20",
    info: "#2c6699",
  },
  sidebar: {
    width: "17rem",
    collapsedWidth: "4.5rem",
  },
} as const;

export const healthCurveTheme = createTheme({
  colors: {
    protectiveNavy,
    horizonTeal,
    horizonOrange,
  },
  primaryColor: "horizonTeal",
  primaryShade: 8,
  fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  headings: {
    fontFamily: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    fontWeight: "750",
  },
  defaultRadius: "md",
  cursorType: "pointer",
  focusRing: "always",
  components: {
    Button: Button.extend({
      defaultProps: {
        radius: "md",
        size: "md",
      },
    }),
  },
  other: healthCurveTokens,
});

export const healthCurveCssVariables: CSSVariablesResolver = () => ({
  variables: {
    "--hc-sidebar-width": healthCurveTokens.sidebar.width,
    "--hc-sidebar-collapsed-width": healthCurveTokens.sidebar.collapsedWidth,
  },
  light: {
    "--hc-color-canvas": healthCurveTokens.canvas,
    "--hc-color-surface": healthCurveTokens.surface,
    "--hc-color-surface-muted": healthCurveTokens.surfaceMuted,
    "--hc-color-text": healthCurveTokens.text,
    "--hc-color-text-muted": healthCurveTokens.textMuted,
    "--hc-color-border": healthCurveTokens.border,
    "--hc-color-focus": healthCurveTokens.focus,
    "--hc-color-success": healthCurveTokens.status.success,
    "--hc-color-warning": healthCurveTokens.status.warning,
    "--hc-color-error": healthCurveTokens.status.error,
    "--hc-color-info": healthCurveTokens.status.info,
  },
  dark: {},
});
