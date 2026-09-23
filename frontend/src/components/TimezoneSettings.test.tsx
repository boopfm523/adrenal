import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { sessionStore } from "../api/session";
import { TimezoneSettings } from "./TimezoneSettings";

const session = {
  csrfToken: "synthetic-csrf",
  user: {
    email: "owner@example.test",
    displayName: null,
    defaultTimezone: "Europe/London",
    currentTimezone: "America/Denver",
  },
};

const AWAY = {
  current_timezone: "America/Denver",
  current_abbreviation: "MDT",
  current_local_time: "2026-09-20T09:15:00",
  current_utc_offset_minutes: -360,
  home_timezone: "Europe/London",
  is_home: false,
  stays: [
    {
      id: "66666666-6666-4666-8666-666666666666",
      timezone: "America/Denver",
      abbreviation: "MDT",
      started_at: "2026-09-18T15:00:00Z",
      started_at_local: "2026-09-18T09:00:00",
      utc_offset_minutes: -360,
      source: "telegram",
      label: "Denver trip",
    },
  ],
};

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function renderSettings(): void {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TimezoneSettings />
    </QueryClientProvider>,
  );
}

describe("timezone settings", () => {
  beforeEach(() => { sessionStore.set(session); });
  afterEach(() => { sessionStore.clear(); });

  it("shows where you are, where home is, and how you got there", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(AWAY), { headers: { "Content-Type": "application/json" } }),
    );
    renderSettings();

    expect(await screen.findByText(/America\/Denver \(MDT\), local time 09:15/)).toBeVisible();
    // Home and current are separate facts; conflating them is the original bug.
    expect(screen.getByText("Europe/London")).toBeVisible();
    expect(screen.getByText(/recorded as away from home/)).toBeVisible();
    const stays = screen.getByRole("region", { name: "Recorded timezone stays table" });
    expect(stays).toHaveTextContent("Telegram");
    expect(stays).toHaveTextContent("Denver trip");
  });

  it("records a stay and says so plainly", async () => {
    const requests: { url: string; method: string; body: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      requests.push({
        url,
        method: init?.method ?? "GET",
        body: init?.body === undefined ? null : JSON.parse(init.body as string),
      });
      if (url.endsWith("/auth/me")) {
        return Promise.resolve(new Response(JSON.stringify({
          csrf_token: "synthetic-csrf",
          email: "owner@example.test",
          display_name: null,
          default_timezone: "Europe/London",
          current_timezone: "America/Chicago",
        }), { headers: { "Content-Type": "application/json" } }));
      }
      if (init?.method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({
          ...AWAY, recorded: true, current_timezone: "America/Chicago", current_abbreviation: "CDT",
        }), { headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify(AWAY), { headers: { "Content-Type": "application/json" } }));
    });
    renderSettings();

    await userEvent.type(screen.getByLabelText("Where are you now?"), "Chicago");
    await userEvent.type(screen.getByLabelText("Optional label"), "Layover");
    await userEvent.click(screen.getByRole("button", { name: "Record this timezone" }));

    expect(await screen.findByText("Recording in America/Chicago from now on.")).toBeVisible();
    const post = requests.find((request) => request.method === "POST");
    expect(post?.url).toBe("/api/v1/settings/timezone");
    expect(post?.body).toEqual({ timezone: "Chicago", label: "Layover" });
    // The session carries the current zone, so a stale one would leave the rest of
    // the app labelling today in the zone just left.
    expect(requests.some((request) => request.url.endsWith("/auth/me"))).toBe(true);
  });

  it("says nothing changed when the zone is restated", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (init?.method === "POST") {
        return Promise.resolve(new Response(JSON.stringify({ ...AWAY, recorded: false }), { headers: { "Content-Type": "application/json" } }));
      }
      if (requestUrl(input).endsWith("/auth/me")) {
        return Promise.resolve(new Response(JSON.stringify({
          csrf_token: "synthetic-csrf", email: "owner@example.test", display_name: null,
          default_timezone: "Europe/London", current_timezone: "America/Denver",
        }), { headers: { "Content-Type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify(AWAY), { headers: { "Content-Type": "application/json" } }));
    });
    renderSettings();

    await userEvent.type(screen.getByLabelText("Where are you now?"), "America/Denver");
    await userEvent.click(screen.getByRole("button", { name: "Record this timezone" }));

    expect(await screen.findByText("You were already recorded as being in America/Denver. Nothing changed."))
      .toBeVisible();
  });

  it("explains an unknown place without claiming anything moved", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      if (init?.method === "POST") {
        return Promise.resolve(new Response(
          JSON.stringify({ detail: { code: "invalid_timezone", message: "unknown" } }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ));
      }
      return Promise.resolve(new Response(JSON.stringify(AWAY), { headers: { "Content-Type": "application/json" } }));
    });
    renderSettings();

    await userEvent.type(screen.getByLabelText("Where are you now?"), "Mars");
    await userEvent.click(screen.getByRole("button", { name: "Record this timezone" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Nothing changed");
  });

  it("asks which zone when a place is ambiguous", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      if (init?.method === "POST") {
        return Promise.resolve(new Response(
          JSON.stringify({ detail: { code: "ambiguous_place", candidates: ["America/Denver", "America/Chicago"] } }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ));
      }
      return Promise.resolve(new Response(JSON.stringify(AWAY), { headers: { "Content-Type": "application/json" } }));
    });
    renderSettings();

    await userEvent.type(screen.getByLabelText("Where are you now?"), "Somewhere");
    await userEvent.click(screen.getByRole("button", { name: "Record this timezone" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("matches more than one timezone");
  });

  it("says travel has never been recorded rather than showing an empty table", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({
        current_timezone: "Europe/London", current_abbreviation: "BST",
        current_local_time: "2026-09-20T16:15:00", current_utc_offset_minutes: 60,
        home_timezone: "Europe/London", is_home: true, stays: [],
      }),
      { headers: { "Content-Type": "application/json" } },
    ));
    renderSettings();

    expect(await screen.findByText("No travel has been recorded. Times use your home timezone.")).toBeVisible();
    expect(screen.getByText("You are recorded as being at home.")).toBeVisible();
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Recorded timezone stays table" })).toBeNull();
    });
  });
});
