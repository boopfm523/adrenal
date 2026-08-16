import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppLayout } from "./components/AppLayout";
import { HealthCurveProvider } from "./components/HealthCurveProvider";
import { RouteLoadBoundary, RouteLoadingStatus } from "./components/RouteLoadBoundary";
import { NotFoundPage } from "./pages/FoundationPages";
import { LoginPage } from "./pages/LoginPage";

const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage").then((module) => ({ default: module.AnalyticsPage })));
const TodayPage = lazy(() => import("./pages/TodayPage").then((module) => ({ default: module.TodayPage })));
const TimelinePage = lazy(() => import("./pages/TimelinePage").then((module) => ({ default: module.TimelinePage })));
const DosesPage = lazy(() => import("./pages/DosesPage").then((module) => ({ default: module.DosesPage })));
const SymptomsDiaryPage = lazy(() => import("./pages/SymptomsDiaryPage").then((module) => ({ default: module.SymptomsDiaryPage })));
const PlanPage = lazy(() => import("./pages/PlanPage").then((module) => ({ default: module.PlanPage })));
const EpisodesPage = lazy(() => import("./pages/EpisodesPage").then((module) => ({ default: module.EpisodesPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));
const DataQualityPage = lazy(() => import("./pages/DataQualityPage").then((module) => ({ default: module.DataQualityPage })));
const ReportsPage = lazy(() => import("./pages/ReportsPage").then((module) => ({ default: module.ReportsPage })));
const HelpPage = lazy(() => import("./pages/HelpPage").then((module) => ({ default: module.HelpPage })));
const HealthDataPage = lazy(() => import("./pages/HealthDataPage").then((module) => ({ default: module.HealthDataPage })));
const LabsPage = lazy(() => import("./pages/LabsPage").then((module) => ({ default: module.LabsPage })));
const ChatPage = lazy(() => import("./pages/ChatPage").then((module) => ({ default: module.ChatPage })));

function route(element: React.JSX.Element): React.JSX.Element {
  return (
    <RouteLoadBoundary>
      <Suspense fallback={<RouteLoadingStatus />}>
        {element}
      </Suspense>
    </RouteLoadBoundary>
  );
}

export function App(): React.JSX.Element {
  return (
    <HealthCurveProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppLayout />}>
            <Route index element={<Navigate to="/healthcurve" replace />} />
            <Route path="/healthcurve" element={route(<AnalyticsPage />)} />
            <Route path="/today" element={route(<TodayPage />)} />
            <Route path="/timeline" element={route(<TimelinePage />)} />
            <Route path="/doses" element={route(<DosesPage />)} />
            <Route path="/symptoms-diary" element={route(<SymptomsDiaryPage />)} />
            <Route path="/plan" element={route(<PlanPage />)} />
            <Route path="/episodes" element={route(<EpisodesPage />)} />
            <Route path="/analytics" element={<Navigate to="/healthcurve" replace />} />
            <Route path="/settings" element={route(<SettingsPage />)} />
            <Route path="/data-quality" element={route(<DataQualityPage />)} />
            <Route path="/reports" element={route(<ReportsPage />)} />
            <Route path="/help" element={route(<HelpPage />)} />
            <Route path="/health-data" element={route(<HealthDataPage />)} />
            <Route path="/labs" element={route(<LabsPage />)} />
            <Route path="/chat" element={route(<ChatPage />)} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </HealthCurveProvider>
  );
}
