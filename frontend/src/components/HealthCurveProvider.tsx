import { MantineProvider } from "@mantine/core";
import type { PropsWithChildren } from "react";

import { healthCurveCssVariables, healthCurveTheme } from "../theme";

export function HealthCurveProvider({ children }: PropsWithChildren): React.JSX.Element {
  return (
    <MantineProvider theme={healthCurveTheme} cssVariablesResolver={healthCurveCssVariables} forceColorScheme="light">
      {children}
    </MantineProvider>
  );
}
