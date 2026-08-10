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
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { DataQualityPage } from "./pages/DataQualityPage";
import { ReportsPage } from "./pages/ReportsPage";
import { HelpPage } from "./pages/HelpPage";

const placeholders = [
  ["/health-data", "Health data", "Review sleep, vitals, activity, and laboratory records with missingness shown."],
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
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/data-quality" element={<DataQualityPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/help" element={<HelpPage />} />
          {placeholders.map(([path, title, description]) => (
            <Route key={path} path={path} element={<PlaceholderPage title={title} description={description} />} />
          ))}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
