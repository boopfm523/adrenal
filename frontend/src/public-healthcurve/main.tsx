import "@mantine/core/styles.css";
import "../styles.css";
import "./public.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { App } from "./App";

const root = document.getElementById("root");
if (root === null) throw new Error("Public HealthCurve root element is missing");

createRoot(root).render(
  <StrictMode>
    <HealthCurveProvider>
      <App />
    </HealthCurveProvider>
  </StrictMode>,
);
