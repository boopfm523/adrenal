import "@mantine/core/styles.css";

import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { healthCurveCssVariables, healthCurveTheme } from "./theme";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("HealthCurve application root is missing");

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 0,
      gcTime: 0,
      retry: false,
      refetchOnWindowFocus: true,
    },
    mutations: { retry: false },
  },
});

createRoot(root).render(
  <StrictMode>
    <MantineProvider theme={healthCurveTheme} cssVariablesResolver={healthCurveCssVariables} forceColorScheme="light">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
);
