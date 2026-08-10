import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/context";

const navigation = [
  ["Today", "/today"],
  ["Timeline", "/timeline"],
  ["Doses", "/doses"],
  ["Plan", "/plan"],
  ["Episodes", "/episodes"],
  ["Symptoms & diary", "/symptoms-diary"],
  ["Health data", "/health-data"],
  ["Labs", "/labs"],
  ["Analytics", "/analytics"],
  ["Reports", "/reports"],
  ["Data quality", "/data-quality"],
  ["Settings & privacy", "/settings"],
  ["Help", "/help"],
] as const;

export function AppLayout(): React.JSX.Element {
  const { session, signOut } = useAuth();

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="app-header">
        <NavLink className="brand" to="/today">HealthCurve</NavLink>
        <a className="emergency-link" href="/emergency">
          <span aria-hidden="true">!</span> Emergency plan
        </a>
        <div className="session-menu">
          <span>{session?.user.displayName ?? "Owner"}</span>
          <button type="button" onClick={() => { void signOut(); }}>Sign out</button>
        </div>
      </header>
      <nav className="primary-navigation" aria-label="Primary">
        {navigation.map(([label, path]) => (
          <NavLink key={path} to={path} className={({ isActive }) => isActive ? "active" : undefined}>
            {label}
          </NavLink>
        ))}
      </nav>
      <main id="main-content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
