import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";

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

function CurrentQuery(): React.JSX.Element {
  return <output aria-label="Current query">{useLocation().search}</output>;
}

function BrowserBack(): React.JSX.Element {
  const navigate = useNavigate();
  return <button type="button" onClick={() => { void navigate(-1); }}>Browser back</button>;
}

function renderPage(initialEntry = "/timeline"): void {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}>
          <TimelinePage />
          <CurrentQuery />
          <BrowserBack />
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Timeline page", () => {
  it("shows provenance and only requests sensitive entries after explicit reveal", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      timezone: "America/New_York",
      page: { page: 1, page_size: 25, total_items: 2, total_pages: 1 },
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
      }, {
        id: "33333333-3333-4333-8333-333333333333",
        category: "fact",
        event_type: "symptom",
        summary: "Synthetic later symptom severity 2/10",
        is_sensitive: false,
        time: {
          occurred_at: "2026-08-09T15:00:00Z",
          local_time: "2026-08-09T11:00:00",
          timezone: "America/New_York",
          utc_offset_minutes: -240,
        },
        provenance: {
          recorded_at: "2026-08-10T12:00:00Z",
          source_type: "web",
          confirmation_state: "direct",
          supersedes_id: null,
          correction_reason: null,
          is_correction: false,
        },
      }].reverse(),
    }), { headers: { "Content-Type": "application/json" } }));

    renderPage();

    expect(await screen.findByText("Synthetic medicine 10 mg")).toBeVisible();
    expect(screen.getByText("2026-08-09 07:00")).toBeVisible();
    expect(screen.getByText("telegram")).toBeVisible();
    expect(screen.getByText("user confirmed")).toBeVisible();
    expect(screen.getByRole("table", { name: /latest first/i })).toBeVisible();
    expect(screen.getByLabelText("Order")).toHaveValue("desc");
    const region = screen.getByRole("region", { name: "Timeline records table" });
    expect(region).toHaveAttribute("tabindex", "0");
    const [, firstRow, secondRow] = within(region).getAllByRole("row");
    if (firstRow === undefined || secondRow === undefined) throw new Error("timeline rows missing");
    expect(within(firstRow).getByText("Synthetic later symptom severity 2/10")).toBeVisible();
    expect(within(firstRow).getByText("Original record")).toBeVisible();
    expect(within(secondRow).getByText("Synthetic medicine 10 mg")).toBeVisible();
    const firstInput = fetchMock.mock.calls[0]?.[0];
    const firstUrl = firstInput === undefined ? "" : requestUrl(firstInput);
    expect(firstUrl).not.toContain("include_sensitive");
    expect(firstUrl).toContain("sort_order=desc");

    await userEvent.click(screen.getByRole("checkbox", { name: "Include sensitive diary entries" }));
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("include_sensitive=true"))).toBe(true);
    });
    expect(screen.getByLabelText("Current query")).toHaveTextContent("include_sensitive=true");
    expect(screen.getByLabelText("Current query")).toHaveTextContent("sort_order=desc");

    await userEvent.selectOptions(screen.getByLabelText("Order"), "asc");
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("sort_order=asc"))).toBe(true);
    });
    expect(screen.getByLabelText("Current query")).toHaveTextContent("sort_order=asc");

    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByLabelText("Order")).toHaveValue("desc");
    expect(screen.getByLabelText("Current query")).toBeEmptyDOMElement();

    await userEvent.click(screen.getByRole("button", { name: "Browser back" }));
    await waitFor(() => { expect(screen.getByLabelText("Order")).toHaveValue("asc"); });
    expect(screen.getByRole("checkbox", { name: "Include sensitive diary entries" })).toBeChecked();
    expect(screen.getByLabelText("Current query")).toHaveTextContent("sort_order=asc");
  });

  it("hydrates shareable filter and oldest-first state from the URL", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      timezone: "America/Chicago", page: { page: 1, page_size: 25, total_items: 0, total_pages: 1 }, items: [],
    }), { headers: { "Content-Type": "application/json" } }));

    renderPage("/timeline?types=symptom&local_date_from=2026-08-01&local_date_to=2026-08-02&timezone=America%2FChicago&include_sensitive=true&sort_order=asc");

    expect(screen.getByLabelText("Record type")).toHaveValue("symptom");
    expect(screen.getByLabelText("From date")).toHaveValue("2026-08-01");
    expect(screen.getByLabelText("Through date")).toHaveValue("2026-08-02");
    expect(screen.getByLabelText("Order")).toHaveValue("asc");
    expect(screen.getByRole("checkbox", { name: "Include sensitive diary entries" })).toBeChecked();
    await waitFor(() => {
      const firstInput = fetchMock.mock.calls[0]?.[0];
      expect(firstInput === undefined ? "" : requestUrl(firstInput)).toContain("sort_order=asc");
    });
  });

  it("links a single filtered day directly into HealthCurve", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      timezone: "America/Chicago", page: { page: 1, page_size: 25, total_items: 0, total_pages: 1 }, items: [],
    }), { headers: { "Content-Type": "application/json" } }));

    renderPage("/timeline?local_date_from=2026-08-01&local_date_to=2026-08-01&timezone=America%2FChicago&sort_order=desc");

    expect(screen.getByRole("link", { name: "Review 2026-08-01 in HealthCurve" })).toHaveAttribute("href", "/healthcurve?day=2026-08-01&timezone=America%2FChicago");
  });

  it("distinguishes filtered-to-zero from a new record", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      timezone: "America/New_York", page: { page: 1, page_size: 25, total_items: 0, total_pages: 1 }, items: [],
    }), { headers: { "Content-Type": "application/json" } })));
    renderPage();
    expect(await screen.findByRole("heading", { name: "No records yet" })).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText("Record type"), "symptom");
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    expect(await screen.findByRole("heading", { name: "No records match these filters" })).toBeVisible();
  });

  it("labels environmental context separately from health facts and AI", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      timezone: "America/New_York",
      page: { page: 1, page_size: 25, total_items: 1, total_pages: 1 },
      items: [{
        id: "22222222-2222-4222-8222-222222222222",
        category: "fact",
        event_type: "context",
        summary: "Boston · Synthetic clear",
        is_sensitive: false,
        time: {
          occurred_at: "2026-08-09T12:00:00Z",
          local_time: "2026-08-09T08:00:00",
          timezone: "America/New_York",
          utc_offset_minutes: -240,
        },
        provenance: {
          recorded_at: "2026-08-09T12:01:00Z",
          source_type: "web",
          confirmation_state: "direct",
          supersedes_id: null,
          correction_reason: null,
          is_correction: false,
        },
      }],
    }), { headers: { "Content-Type": "application/json" } }));

    renderPage();
    const summary = await screen.findByText("Boston · Synthetic clear");
    const row = summary.closest("tr[data-category='context']");
    if (row === null) throw new Error("context row missing");
    expect(within(row as HTMLElement).getByText("Environmental context", { exact: false })).toBeVisible();
    expect(within(row as HTMLElement).getByText(/not a symptom, dose, physician instruction, or AI conclusion/)).toBeVisible();
    expect(screen.getByRole("option", { name: "Environmental context" })).toHaveValue("context");
  });

  it("moves through bounded pages while preserving URL filter state", async () => {
    const item = {
      id: "11111111-1111-4111-8111-111111111111",
      category: "fact",
      event_type: "dose",
      summary: "Synthetic medicine 5 mg",
      is_sensitive: false,
      time: {
        occurred_at: "2026-08-09T11:00:00Z",
        local_time: "2026-08-09T07:00:00",
        timezone: "America/New_York",
        utc_offset_minutes: -240,
      },
      provenance: {
        recorded_at: "2026-08-09T11:01:00Z",
        source_type: "web",
        confirmation_state: "direct",
        supersedes_id: null,
        correction_reason: null,
        is_correction: false,
      },
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const secondPage = requestUrl(input).includes("page=2");
      return Promise.resolve(new Response(JSON.stringify({
        timezone: "America/New_York",
        page: { page: secondPage ? 2 : 1, page_size: 25, total_items: 26, total_pages: 2 },
        items: [item],
      }), { headers: { "Content-Type": "application/json" } }));
    });

    renderPage("/timeline?types=dose&timezone=America%2FNew_York&sort_order=desc");

    expect(await screen.findByText("Showing 1–25 of 26. Page 1 of 2.")).toBeVisible();
    const pagination = screen.getByRole("navigation", { name: "Timeline records pagination" });
    expect(within(pagination).getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => requestUrl(input).includes("page=2"))).toBe(true);
    });
    expect(await screen.findByText("Showing 26–26 of 26. Page 2 of 2.")).toBeVisible();
    expect(screen.getByLabelText("Current query")).toHaveTextContent("types=dose");
    expect(screen.getByLabelText("Current query")).toHaveTextContent("page=2");
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});
