import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { sessionStore } from "../api/session";
import { AuthContext, type AuthContextValue } from "../auth/context";
import { SettingsPage } from "./SettingsPage";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "Europe/London" } };
const auth: AuthContextValue = { status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() };
function requestUrl(input: RequestInfo | URL): string { if (typeof input === "string") return input; if (input instanceof URL) return input.href; return input.url; }

describe("Settings and privacy page", () => {
  beforeEach(() => { sessionStore.set(session); });
  afterEach(() => { sessionStore.clear(); });

  it("discloses retention and sends reauthenticated privacy actions", async () => {
    const submittedPassword = ["synthetic", "password"].join("-");
    const requests: { url: string; method: string; body: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => { const url = requestUrl(input); requests.push({ url, method: init?.method ?? "GET", body: init?.body === undefined ? null : JSON.parse(init.body as string) }); if (url.endsWith("/integrations/garmin/status")) return Promise.resolve(new Response(JSON.stringify({ configured: true, state: "connected", last_success_at: "2026-08-10T12:00:00Z", checkpoint_date: "2026-08-10", capabilities: { steps: "available", stress: "unavailable" }, last_error_code: null, client_version: "synthetic", latest_sync_status: "completed", latest_sync_origin: "manual_refresh", latest_sync_warning_codes: [] }), { headers: { "Content-Type": "application/json" } })); if (url.endsWith("/integrations/garmin/disconnect-preview")) return Promise.resolve(new Response(JSON.stringify({ state: "connected", automatic_fact_rows: 5, reviewed_import_fact_rows: 2, sync_run_rows: 1, delete_data_confirmation: "DISCONNECT GARMIN AND DELETE DATA", retain_data_confirmation: "DISCONNECT GARMIN" }), { headers: { "Content-Type": "application/json" } })); if (url.includes("/integrations/garmin/sync")) return Promise.resolve(new Response(JSON.stringify({ job_id: "synthetic-job", status: "queued", disposition: url.includes("refresh=true") ? "refresh_queued" : "queued", origin: url.includes("refresh=true") ? "manual_refresh" : "manual", requested_start_date: "2026-08-04", requested_end_date: "2026-08-10", cooldown_until: null }), { status: 202, headers: { "Content-Type": "application/json" } })); if (url.endsWith("/privacy/export")) return Promise.resolve(new Response("{}", { headers: { "Content-Type": "application/json" } })); if (url.includes("/privacy/integrations/")) return Promise.resolve(new Response(JSON.stringify({ credentials_deleted: 1, data_rows_deleted: 2, disconnect_requested: url.endsWith("/garmin") }), { headers: { "Content-Type": "application/json" } })); return Promise.resolve(new Response(null, { status: 204 })); });
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:synthetic") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter><SettingsPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    expect(screen.getByText(/Telegram itself may retain messages/)).toBeVisible();
    expect(screen.getByText(/not the raw directive/)).toBeVisible();
    expect(screen.getByText(/Encrypted backups can retain deleted data until/)).toBeVisible();
    expect(screen.getByText(/Structural audit entries recording deletion survive/)).toBeVisible();
    expect(screen.getByText(/Default: coarse location/)).toBeVisible();
    expect(screen.getByText(/Exact location is opt-in per record/)).toBeVisible();

    expect(screen.getByText(/only rounded 0.1-degree coordinates and fixed/)).toBeVisible();
    expect(await screen.findByText("connected")).toBeVisible();
    expect(screen.getByText("Manual refresh")).toBeVisible();
    expect(screen.getByText("5 automatic fact row(s), 2 reviewed import fact row(s), and 1 sync provenance row(s) are currently recorded.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Sync Garmin now" }));
    await waitFor(() => { expect(requests.some((request) => request.url.endsWith("/integrations/garmin/sync") && request.method === "POST")).toBe(true); });
    expect(await screen.findByText("Garmin sync for 2026-08-04 through 2026-08-10 was queued.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Refresh recent Garmin window" }));
    await waitFor(() => { expect(requests.some((request) => request.url.endsWith("/integrations/garmin/sync?refresh=true") && request.method === "POST")).toBe(true); });
    expect(await screen.findByText("A deliberate refresh of 2026-08-04 through 2026-08-10 was queued.")).toBeVisible();

    for (const provider of ["telegram", "weather"] as const) {
      const button = screen.getByRole("button", { name: `Disconnect ${provider}` });
      const form = button.closest("form");
      if (form === null) throw new Error("integration form missing");
      fireEvent.change(within(form).getByLabelText("Current password"), { target: { value: submittedPassword } });
      fireEvent.click(button);
    }
    const garminButton = screen.getByRole("button", { name: "Disconnect Garmin" });
    const garminForm = garminButton.closest("form");
    if (garminForm === null) throw new Error("Garmin disconnect form missing");
    fireEvent.change(within(garminForm).getByLabelText("Current password"), { target: { value: submittedPassword } });
    fireEvent.change(within(garminForm).getByLabelText("Type DISCONNECT GARMIN AND DELETE DATA"), { target: { value: "DISCONNECT GARMIN AND DELETE DATA" } });
    fireEvent.click(garminButton);
    await waitFor(() => { expect(requests.filter((request) => request.url.includes("/privacy/integrations/")).length).toBe(3); });
    expect(requests.find((request) => request.url.endsWith("/privacy/integrations/garmin"))?.body).toMatchObject({ delete_data: true, confirmation: "DISCONNECT GARMIN AND DELETE DATA" });

    const exportButton = screen.getByRole("button", { name: "Download private export" });
    const exportForm = exportButton.closest("form");
    if (exportForm === null) throw new Error("export form missing");
    fireEvent.change(within(exportForm).getByLabelText("Current password"), { target: { value: submittedPassword } });
    fireEvent.click(exportButton);
    await waitFor(() => { expect(requests.some((request) => request.url.endsWith("/privacy/export") && (request.body as { password?: string }).password === submittedPassword)).toBe(true); });

    const deleteButton = screen.getByRole("button", { name: "Permanently delete account" });
    const deleteForm = deleteButton.closest("form");
    if (deleteForm === null) throw new Error("account deletion form missing");
    fireEvent.change(within(deleteForm).getByLabelText("Current password"), { target: { value: submittedPassword } });
    fireEvent.change(within(deleteForm).getByLabelText("Type DELETE MY HEALTHCURVE ACCOUNT"), { target: { value: "DELETE MY HEALTHCURVE ACCOUNT" } });
    fireEvent.click(deleteButton);
    await waitFor(() => { expect(requests.some((request) => request.url.endsWith("/privacy/account") && (request.body as { confirmation?: string }).confirmation === "DELETE MY HEALTHCURVE ACCOUNT")).toBe(true); });
  });

  it("gates exact coordinates and supports manual context review and deletion", async () => {
    const requests: { url: string; method: string; body: unknown }[] = [];
    const recordId = "33333333-3333-4333-8333-333333333333";
    const deletionPassword = ["synthetic", "password"].join("-");
    const recorded = [{
      category: "fact",
      id: recordId,
      location_precision: "coarse",
      coarse_location_label: "Synthetic Boston",
      exact_location_consent: false,
      time: { occurred_at: "2026-08-09T12:00:00Z", local_time: "2026-08-09T08:00:00", timezone: "America/New_York", utc_offset_minutes: -240 },
      provenance: { recorded_at: "2026-08-09T12:01:00Z", source_type: "web", confirmation_state: "direct", supersedes_id: null, correction_reason: null, is_correction: false },
    }];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? null : JSON.parse(init.body as string);
      requests.push({ url, method, body });
      if (url.includes("/context-events?") && method === "GET") return Promise.resolve(new Response(JSON.stringify({ items: recorded, revisions: [], page: { page: 1, page_size: 25, total_items: 1, total_pages: 1 } }), { headers: { "Content-Type": "application/json" } }));
      if (url.endsWith("/context-events") && method === "POST") return Promise.resolve(new Response(JSON.stringify(recorded[0]), { status: 201, headers: { "Content-Type": "application/json" } }));
      return Promise.resolve(new Response(null, { status: 204 }));
    });
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter><SettingsPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    expect(await screen.findByRole("rowheader", { name: "Synthetic Boston" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Context records table" })).toBeVisible();
    expect(screen.getByText("Weather not recorded—not zero and not inferred.")).toBeVisible();
    const precision = screen.getByLabelText("Location precision");
    expect(precision).toHaveValue("coarse");
    fireEvent.change(precision, { target: { value: "exact" } });
    expect(screen.queryByLabelText("Latitude")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Record context" })).toBeDisabled();
    const consent = screen.getByRole("checkbox", { name: /I consent to storing exact coordinates/ });
    consent.focus();
    await userEvent.keyboard("[Space]");
    expect(screen.getByLabelText("Latitude")).toBeEnabled();
    expect(screen.getByLabelText("Longitude")).toBeEnabled();
    fireEvent.change(precision, { target: { value: "coarse" } });

    fireEvent.change(screen.getByLabelText("From date"), { target: { value: "2026-08-09" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply context filters" }));
    await waitFor(() => { expect(requests.some((request) => request.method === "GET" && request.url.includes("local_date_from=2026-08-09") && request.url.includes("timezone=Europe%2FLondon"))).toBe(true); });

    fireEvent.change(screen.getByLabelText("Experienced local date and time"), { target: { value: "2026-08-09T08:30" } });
    fireEvent.change(screen.getByLabelText("Coarse location label"), { target: { value: "Synthetic Cambridge" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Add a manual weather observation" }));
    fireEvent.change(screen.getByLabelText("Observed at (UTC)"), { target: { value: "2026-08-09T12:30" } });
    fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "24.50" } });
    fireEvent.click(screen.getByRole("button", { name: "Record context" }));
    await waitFor(() => {
      const created = requests.find((request) => request.url.endsWith("/context-events") && request.method === "POST");
      expect(created?.body).toMatchObject({
        location_precision: "coarse",
        coarse_location_label: "Synthetic Cambridge",
        exact_location_consent: false,
        weather_provider: "manual",
        weather_observed_at: "2026-08-09T12:30:00Z",
        temperature: "24.50",
        temperature_unit: "c",
      });
    });

    const deleteButton = screen.getByRole("button", { name: "Delete this context record" });
    const deleteForm = deleteButton.closest("form");
    if (deleteForm === null) throw new Error("context deletion form missing");
    fireEvent.change(within(deleteForm).getByLabelText("Current password"), { target: { value: deletionPassword } });
    fireEvent.click(deleteButton);
    await waitFor(() => {
      expect(requests.some((request) => request.url.endsWith(`/context-events/${recordId}`) && request.method === "DELETE" && (request.body as { password?: string }).password === deletionPassword)).toBe(true);
    });
  });

  it("offers no HealthCurve MFA or passkey controls", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter><SettingsPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    expect(screen.queryByText(/multi-factor authentication|recovery code|passkey/i)).not.toBeInTheDocument();
    expect(screen.getByText(/reachable only through the approved Tailscale network/)).toBeVisible();
  });
});
