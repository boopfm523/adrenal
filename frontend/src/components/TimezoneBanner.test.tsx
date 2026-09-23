import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthContext, type AuthContextValue } from "../auth/context";
import * as time from "../time";
import { TimezoneBanner } from "./TimezoneBanner";

const DISMISSED_KEY = "hc.timezone-prompt-dismissed";

function authFor(currentTimezone: string): AuthContextValue {
  return {
    status: "authenticated",
    session: {
      csrfToken: "synthetic-csrf",
      user: {
        email: "owner@example.test",
        displayName: "Synthetic Owner",
        defaultTimezone: "Europe/London",
        currentTimezone,
      },
    },
    signIn: vi.fn(),
    signOut: vi.fn(),
  };
}

function mockDeviceTimezone(zone: string | null): void {
  vi.spyOn(time, "deviceTimezone").mockReturnValue(zone);
}

function renderBanner(currentTimezone: string): void {
  render(
    <AuthContext.Provider value={authFor(currentTimezone)}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TimezoneBanner />
      </QueryClientProvider>
    </AuthContext.Provider>,
  );
}

describe("travel timezone prompt", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("stays silent when the device agrees with the recorded zone", () => {
    mockDeviceTimezone("Europe/London");
    renderBanner("Europe/London");
    expect(screen.queryByRole("region", { name: /different timezone/i })).toBeNull();
  });

  it("offers the switch on a genuine mismatch without making it", () => {
    mockDeviceTimezone("America/Denver");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderBanner("Europe/London");

    const banner = screen.getByRole("region", { name: "This device is in a different timezone" });
    expect(banner).toBeVisible();
    // Both zones are named: the owner has to be able to tell which way the offer goes.
    expect(banner).toHaveTextContent("Europe/London");
    expect(banner).toHaveTextContent("America/Denver");
    // Nothing is recorded until the owner says so.
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Switch to America/Denver" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Keep Europe/London" })).toBeVisible();
  });

  it("records the stay when the offer is accepted from the keyboard", async () => {
    mockDeviceTimezone("America/Denver");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    renderBanner("Europe/London");

    const accept = screen.getByRole("button", { name: "Switch to America/Denver" });
    accept.focus();
    await userEvent.keyboard("[Enter]");

    await waitFor(() => { expect(fetchSpy).toHaveBeenCalled(); });
    const [path, init] = fetchSpy.mock.calls[0] ?? [];
    expect(path).toBe("/api/v1/settings/timezone");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ timezone: "America/Denver" }));
  });

  it("remembers a dismissal for that device zone", async () => {
    mockDeviceTimezone("America/Denver");
    renderBanner("Europe/London");

    await userEvent.click(screen.getByRole("button", { name: "Keep Europe/London" }));

    expect(screen.queryByRole("region", { name: /different timezone/i })).toBeNull();
    expect(window.localStorage.getItem(DISMISSED_KEY)).toBe("America/Denver");
  });

  it("asks again at the next destination", () => {
    window.localStorage.setItem(DISMISSED_KEY, "America/Denver");
    mockDeviceTimezone("Asia/Tokyo");
    renderBanner("Europe/London");

    // Declining Denver said nothing about Tokyo.
    expect(screen.getByRole("region", { name: "This device is in a different timezone" })).toBeVisible();
  });

  it("says nothing when the platform cannot report a zone", () => {
    mockDeviceTimezone(null);
    renderBanner("Europe/London");
    expect(screen.queryByRole("region", { name: /different timezone/i })).toBeNull();
  });
});
