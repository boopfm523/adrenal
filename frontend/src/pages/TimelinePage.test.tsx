import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AuthContext } from "../auth/context";
import { TimelinePage } from "./TimelinePage";

const session = {
  csrfToken: "synthetic-csrf",
  user: { email: "owner@example.test", displayName: "Synthetic Owner", defaultTimezone: "America/New_York" },
};

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function renderPage(): void {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}>
          <TimelinePage />
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Timeline page", () => {
  it("shows provenance and only requests sensitive entries after explicit reveal", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      timezone: "America/New_York",
      next_cursor: null,
      items: [{
        id: "11111111-1111-4111-8111-111111111111",
        category: "fact",
        event_type: "dose",
        summary: "Synthetic medicine 10.0000 mg",
        is_sensitive: false,
        time: {
          occurred_at: "2026-08-09T11:00:00Z",
          local_time: "2026-08-09T07:00:00",
          timezone: "America/New_York",
          utc_offset_minutes: -240,
        },
        provenance: {
          recorded_at: "2026-08-09T11:01:00Z",
          source_type: "telegram",
          confirmation_state: "user_confirmed",
          supersedes_id: null,
          correction_reason: null,
          is_correction: false,
        },
      }],
    }), { headers: { "Content-Type": "application/json" } }));

    renderPage();

    expect(await screen.findByText("Synthetic medicine 10.0000 mg")).toBeVisible();
    expect(screen.getByText("2026-08-09 07:00 · America/New_York")).toBeVisible();
    expect(screen.getByText("telegram")).toBeVisible();
    expect(screen.getByText("user confirmed")).toBeVisible();
    expect(screen.getByText("Original record")).toBeVisible();
    const firstInput = fetchMock.mock.calls[0]?.[0];
    expect(firstInput === undefined ? "" : requestUrl(firstInput)).not.toContain("include_sensitive");

    await userEvent.click(screen.getByRole("checkbox", { name: "Include sensitive diary entries" }));
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("include_sensitive=true"))).toBe(true);
    });
  });

  it("distinguishes filtered-to-zero from a new record", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      timezone: "America/New_York", next_cursor: null, items: [],
    }), { headers: { "Content-Type": "application/json" } })));
    renderPage();
    expect(await screen.findByRole("heading", { name: "No records yet" })).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText("Record type"), "symptom");
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByRole("heading", { name: "No records match these filters" })).toBeVisible();
  });
});
