import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { sessionStore } from "../api/session";
import { HealthCurveProvider } from "./HealthCurveProvider";
import { HealthRecordAiSettings } from "./HealthRecordAiSettings";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York", currentTimezone: "America/New_York" } };

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input.url;
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}

describe("Imported health records in chat and AI", () => {
  afterEach(() => { sessionStore.clear(); });

  it("starts every category off, never lists medications, and saves one choice with CSRF", async () => {
    let labsEnabled = false;
    const writes: { url: string; csrf: string | null; body: unknown }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = requestUrl(input);
      if (init?.method === "PUT") {
        writes.push({ url, csrf: new Headers(init.headers).get("X-CSRF-Token"), body: JSON.parse(init.body as string) });
        labsEnabled = true;
        return Promise.resolve(json({ category: "labs", label: "Lab results", enabled: true, has_ai_path: true }));
      }
      return Promise.resolve(json({
        items: [
          { category: "labs", label: "Lab results", enabled: labsEnabled, has_ai_path: true },
          { category: "clinical_notes", label: "Clinical notes, discharge and procedure notes", enabled: false, has_ai_path: false },
        ],
        medications_note: "Imported medication lists are reference history only.",
      }));
    });
    sessionStore.set(session);
    render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><HealthRecordAiSettings /></QueryClientProvider></HealthCurveProvider>);

    const labs = await screen.findByRole("checkbox", { name: "Lab results" });
    expect(labs).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Clinical notes/ })).not.toBeChecked();
    expect(screen.getByText("No chat or AI feature reads this category yet.")).toBeVisible();
    expect(screen.queryByRole("checkbox", { name: /medication/i })).not.toBeInTheDocument();
    expect(screen.getByText(/reference history only/)).toBeVisible();

    await userEvent.click(labs);
    await waitFor(() => { expect(screen.getByRole("checkbox", { name: "Lab results" })).toBeChecked(); });
    expect(writes).toEqual([{ url: "/api/v1/health-records/ai-visibility/labs", csrf: "synthetic-csrf", body: { enabled: true } }]);
  });
});
