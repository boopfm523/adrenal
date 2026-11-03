import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext } from "../auth/context";
import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { ProceduresPage } from "./ProceduresPage";

const session = {
  csrfToken: "synthetic-csrf",
  user: { email: "owner@example.test", displayName: "Synthetic Owner", defaultTimezone: "America/New_York", currentTimezone: "America/New_York" },
};

function renderPage(): void {
  render(
    <HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/procedures"]}>
        <AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}>
          <ProceduresPage />
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider></HealthCurveProvider>,
  );
}

function lifeEvent(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    id: "99999999-9999-4999-8999-999999999999",
    title: "Synthetic procedure",
    life_category: "surgery",
    description: "Synthetic Hospital - Dr Synthetic",
    is_sensitive: false,
    time: {
      occurred_at: "2024-05-01T16:00:00Z",
      local_time: "2024-05-01T12:00:00",
      timezone: "America/New_York",
      utc_offset_minutes: -240,
    },
    provenance: {
      recorded_at: "2026-08-11T08:00:00Z",
      source_type: "csv_import",
      confirmation_state: "direct",
      supersedes_id: null,
      correction_reason: null,
      is_correction: false,
    },
    ...overrides,
  };
}

describe("Procedures page", () => {
  it("requests only the surgery category and renders dates, titles, and facilities", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      items: [
        lifeEvent({ id: "11111111-1111-4111-8111-111111111111", title: "Earlier procedure", description: "Hospital A - Dr A", time: { ...lifeEvent({}).time as object, local_time: "2020-02-20T12:00:00" } }),
        lifeEvent({ id: "22222222-2222-4222-8222-222222222222", title: "Later procedure", description: "Hospital B - Dr B", time: { ...lifeEvent({}).time as object, local_time: "2026-08-18T12:00:00" } }),
      ],
      revisions: [],
      page: { page: 1, page_size: 25, total_items: 2, total_pages: 1 },
    }), { status: 200 }));

    renderPage();

    await waitFor(() => { expect(screen.getByText("Earlier procedure")).toBeInTheDocument(); });
    expect(screen.getByText("Later procedure")).toBeInTheDocument();
    expect(screen.getByText("Hospital A - Dr A")).toBeInTheDocument();
    expect(screen.getByText("Hospital B - Dr B")).toBeInTheDocument();
    expect(screen.getByText("February 20, 2020")).toBeInTheDocument();
    expect(screen.getByText("August 18, 2026")).toBeInTheDocument();

    const requested = new URL(fetchMock.mock.calls[0]?.[0] as string, "http://localhost");
    expect(requested.pathname.endsWith("/life-events")).toBe(true);
    expect(requested.searchParams.get("category")).toBe("surgery");
  });

  it("shows an empty state when nothing is recorded yet", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      items: [],
      revisions: [],
      page: { page: 1, page_size: 25, total_items: 0, total_pages: 1 },
    }), { status: 200 }));

    renderPage();

    await waitFor(() => { expect(screen.getByText("No procedures recorded yet")).toBeInTheDocument(); });
  });
});
