import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => { const url = requestUrl(input); requests.push({ url, method: init?.method ?? "GET", body: init?.body === undefined ? null : JSON.parse(init.body as string) }); if (url.endsWith("/privacy/export")) return Promise.resolve(new Response("{}", { headers: { "Content-Type": "application/json" } })); if (url.includes("/integrations/")) return Promise.resolve(new Response(JSON.stringify({ credentials_deleted: 1, data_rows_deleted: 2 }), { headers: { "Content-Type": "application/json" } })); return Promise.resolve(new Response(null, { status: 204 })); });
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:synthetic") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthContext.Provider value={auth}><MemoryRouter><SettingsPage /></MemoryRouter></AuthContext.Provider></QueryClientProvider>);

    expect(screen.getByText(/Telegram itself may retain messages/)).toBeVisible();
    expect(screen.getByText(/Encrypted backups can retain deleted data until/)).toBeVisible();
    expect(screen.getByText(/Structural audit entries recording deletion survive/)).toBeVisible();
    expect(screen.getByText(/Location collection is not configured/)).toBeVisible();

    for (const provider of ["garmin", "telegram"] as const) {
      const button = screen.getByRole("button", { name: `Disconnect ${provider}` });
      const form = button.closest("form");
      if (form === null) throw new Error("integration form missing");
      fireEvent.change(within(form).getByLabelText("Current password"), { target: { value: submittedPassword } });
      fireEvent.click(button);
    }
    await waitFor(() => { expect(requests.filter((request) => request.url.includes("/privacy/integrations/")).length).toBe(2); });

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
});
