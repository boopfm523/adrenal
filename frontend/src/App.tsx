import { Navigate, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppLayout } from "./components/AppLayout";
import { PlaceholderPage, NotFoundPage } from "./pages/FoundationPages";
import { LoginPage } from "./pages/LoginPage";
import { TodayPage } from "./pages/TodayPage";
import { TimelinePage } from "./pages/TimelinePage";
import { DosesPage } from "./pages/DosesPage";
import { SymptomsDiaryPage } from "./pages/SymptomsDiaryPage";
import { PlanPage } from "./pages/PlanPage";
import { EpisodesPage } from "./pages/EpisodesPage";

const placeholders = [
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
          <Route path="/timeline" element={<TimelinePage />} />
          <Route path="/doses" element={<DosesPage />} />
          <Route path="/symptoms-diary" element={<SymptomsDiaryPage />} />
          <Route path="/plan" element={<PlanPage />} />
          <Route path="/episodes" element={<EpisodesPage />} />
          {placeholders.map(([path, title, description]) => (
            <Route key={path} path={path} element={<PlaceholderPage title={title} description={description} />} />
          ))}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
