import { AppShell, Box, Burger, Button, Group, ScrollArea, Text } from "@mantine/core";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import protectiveHorizonLogo from "../../../design/logo-concepts/healthcurve-protective-horizon-concept.png";
import { useAuth } from "../auth/context";

const navigation = [
  ["Daily review", "/healthcurve"],
  ["Chat", "/chat"],
  ["Today", "/today"],
  ["Timeline", "/timeline"],
  ["Doses", "/doses"],
  ["Plan", "/plan"],
  ["Episodes", "/episodes"],
  ["Symptoms & Meals", "/symptoms-diary"],
  ["Health data", "/health-data"],
  ["Labs", "/labs"],
  ["Reports", "/reports"],
  ["Data quality", "/data-quality"],
  ["Settings & privacy", "/settings"],
  ["Help", "/help"],
] as const;

// Phones and tablets share one predictable drawer interaction. Width alone is
// deliberately insufficient: a narrow desktop browser still keeps the
// persistent sidebar, while touch-first iPhone and iPad layouts use the drawer.
export const NAVIGATION_DRAWER_BREAKPOINT = "lg" as const;
export const NAVIGATION_DRAWER_MEDIA_QUERY =
  "(max-width: 74.99em) and (any-pointer: coarse)";

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

function SickDayLink(): React.JSX.Element {
  return (
    <a
      className="sick-day-link"
      href="/api/v1/private-documents/sick-day-plan"
      target="_blank"
      rel="noreferrer"
    >
      <span aria-hidden="true">PDF</span> Sick-day plan
    </a>
  );
}

export function AppLayout(): React.JSX.Element {
  const { session, signOut } = useAuth();
  const location = useLocation();
  const [mobileOpened, { close: closeMobile, toggle: toggleMobile }] = useDisclosure(false);
  const usesDrawerNavigation = useMediaQuery(NAVIGATION_DRAWER_MEDIA_QUERY, false);

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
      header={{ height: usesDrawerNavigation ? 64 : 0 }}
      navbar={{
        width: "var(--hc-sidebar-width)",
        breakpoint: usesDrawerNavigation ? NAVIGATION_DRAWER_BREAKPOINT : 0,
        collapsed: { mobile: usesDrawerNavigation && !mobileOpened },
      }}
      padding={0}
    >
      <a className="skip-link" href="#main-content">Skip to main content</a>

      {usesDrawerNavigation ? (
        <AppShell.Header className="mobile-header">
          <Group h="100%" px="md" justify="space-between" wrap="nowrap">
            <Burger opened={mobileOpened} onClick={toggleMobile} aria-label={mobileOpened ? "Close navigation" : "Open navigation"} size="sm" />
            <Brand />
            <Group className="mobile-safety-links" gap="xs" wrap="nowrap">
              <SickDayLink />
              <EmergencyLink />
            </Group>
          </Group>
        </AppShell.Header>
      ) : null}

      <AppShell.Navbar className="sidebar" aria-label="Primary">
        {!usesDrawerNavigation ? (
          <AppShell.Section className="sidebar-brand">
            <Brand />
          </AppShell.Section>
        ) : null}

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
          <Box className="sidebar-safety-links">
            <SickDayLink />
            {!usesDrawerNavigation ? <EmergencyLink /> : null}
          </Box>
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
