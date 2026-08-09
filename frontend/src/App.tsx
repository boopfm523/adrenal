import { Navigate, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppLayout } from "./components/AppLayout";
import { PlaceholderPage, NotFoundPage, TodayPage } from "./pages/FoundationPages";
import { LoginPage } from "./pages/LoginPage";

const placeholders = [
  ["/timeline", "Timeline", "Review the chronological record with provenance and correction history."],
  ["/plan", "Plan & doses", "Review physician-approved plans separately from actual recorded doses."],
  ["/episodes", "Episodes", "Review stress, up-dose, and emergency-injection episodes without causal claims."],
  ["/symptoms-diary", "Symptoms & diary", "Review subjective symptoms and private life events."],
  ["/health-data", "Health data", "Review sleep, vitals, activity, and laboratory records with missingness shown."],
  ["/analytics", "Analytics", "Explore deterministic metrics and clearly labeled associations."],
  ["/reports", "Reports", "Build snapshots for physician conversations."],
  ["/data-quality", "Data quality", "Review drafts, ambiguities, gaps, duplicates, and import health."],
  ["/settings", "Settings & privacy", "Manage access, integrations, retention, exports, and deletion."],
] as const;

export function App(): React.JSX.Element {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route index element={<Navigate to="/today" replace />} />
          <Route path="/today" element={<TodayPage />} />
          {placeholders.map(([path, title, description]) => (
            <Route key={path} path={path} element={<PlaceholderPage title={title} description={description} />} />
          ))}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
