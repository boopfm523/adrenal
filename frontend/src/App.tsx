import { Navigate, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppLayout } from "./components/AppLayout";
import { NotFoundPage } from "./pages/FoundationPages";
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
import { HealthDataPage } from "./pages/HealthDataPage";
import { LabsPage } from "./pages/LabsPage";

export function App(): React.JSX.Element {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route index element={<Navigate to="/healthcurve" replace />} />
          <Route path="/healthcurve" element={<AnalyticsPage />} />
          <Route path="/today" element={<TodayPage />} />
          <Route path="/timeline" element={<TimelinePage />} />
          <Route path="/doses" element={<DosesPage />} />
          <Route path="/symptoms-diary" element={<SymptomsDiaryPage />} />
          <Route path="/plan" element={<PlanPage />} />
          <Route path="/episodes" element={<EpisodesPage />} />
          <Route path="/analytics" element={<Navigate to="/healthcurve" replace />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/data-quality" element={<DataQualityPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/help" element={<HelpPage />} />
          <Route path="/health-data" element={<HealthDataPage />} />
          <Route path="/labs" element={<LabsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
