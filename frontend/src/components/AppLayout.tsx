import { AppShell, Box, Burger, Button, Group, ScrollArea, Text } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import protectiveHorizonLogo from "../../../design/logo-concepts/healthcurve-protective-horizon-concept.png";
import { useAuth } from "../auth/context";

const navigation = [
  ["Daily review", "/healthcurve"],
  ["Today", "/today"],
  ["Timeline", "/timeline"],
  ["Doses", "/doses"],
  ["Plan", "/plan"],
  ["Episodes", "/episodes"],
  ["Symptoms & diary", "/symptoms-diary"],
  ["Health data", "/health-data"],
  ["Labs", "/labs"],
  ["Reports", "/reports"],
  ["Data quality", "/data-quality"],
  ["Settings & privacy", "/settings"],
  ["Help", "/help"],
] as const;

function Brand(): React.JSX.Element {
  return (
    <NavLink className="brand" to="/healthcurve" aria-label="HealthCurve.ai home">
      <img className="brand-logo" src={protectiveHorizonLogo} alt="" />
    </NavLink>
  );
}

function EmergencyLink(): React.JSX.Element {
  return (
    <a className="emergency-link" href="/emergency">
      <span aria-hidden="true">!</span> Emergency plan
    </a>
  );
}

export function AppLayout(): React.JSX.Element {
  const { session, signOut } = useAuth();
  const location = useLocation();
  const [mobileOpened, { close: closeMobile, toggle: toggleMobile }] = useDisclosure(false);

  useEffect(() => {
    closeMobile();
  }, [closeMobile, location.pathname]);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key === "Escape") closeMobile();
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => { window.removeEventListener("keydown", closeOnEscape); };
  }, [closeMobile]);

  return (
    <AppShell
      className="app-shell"
      header={{ height: { base: 64, sm: 0 } }}
      navbar={{ width: "var(--hc-sidebar-width)", breakpoint: "sm", collapsed: { mobile: !mobileOpened } }}
      padding={0}
    >
      <a className="skip-link" href="#main-content">Skip to main content</a>

      <AppShell.Header className="mobile-header" hiddenFrom="sm">
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Burger opened={mobileOpened} onClick={toggleMobile} aria-label={mobileOpened ? "Close navigation" : "Open navigation"} size="sm" />
          <Brand />
          <EmergencyLink />
        </Group>
      </AppShell.Header>

      <AppShell.Navbar className="sidebar" aria-label="Primary">
        <AppShell.Section className="sidebar-brand" visibleFrom="sm">
          <Brand />
        </AppShell.Section>

        <AppShell.Section component={ScrollArea} grow className="sidebar-navigation">
          <div className="sidebar-navigation-links">
            {navigation.map(([label, path]) => (
              <NavLink key={path} to={path} className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}>
                {label}
              </NavLink>
            ))}
          </div>
        </AppShell.Section>

        <AppShell.Section className="sidebar-footer">
          <Box visibleFrom="sm"><EmergencyLink /></Box>
          <Text size="sm" fw={650} truncate>{session?.user.displayName ?? "Owner"}</Text>
          <Button variant="light" fullWidth onClick={() => { void signOut(); }}>Sign out</Button>
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main component="main" id="main-content" tabIndex={-1}>
        <div className="main-content-inner">
          <Outlet />
        </div>
      </AppShell.Main>
    </AppShell>
  );
}
