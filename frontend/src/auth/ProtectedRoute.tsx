import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "./context";

export function ProtectedRoute(): React.JSX.Element {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "checking") {
    return <p role="status" className="centered-state">Checking your session…</p>;
  }
  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
