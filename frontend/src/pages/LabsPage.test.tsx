import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AuthContext } from "../auth/context";
import { LabsPage } from "./LabsPage";

const session = { csrfToken: "synthetic-csrf", user: { email: "owner@example.test", displayName: null, defaultTimezone: "America/New_York", mfaEnabled: false } };
const base = { id: "11111111-1111-4111-8111-111111111111", panel_id: "22222222-2222-4222-8222-222222222222", category: "fact", analyte_name: "Cortisol AM", original_value: "10", qualitative_result: null, original_unit: "mcg/dL", original_reference_range: "source range", abnormal_flag: null, normalized_analyte_code: "cortisol", normalized_analyte_name: "Cortisol", normalized_value: "276.0000000000", normalized_unit: "nmol/L", normalization_method: "hc-lab-normalization-v1:cortisol:27.6", specimen_time: { occurred_at: "2026-08-09T12:00:00Z", local_time: "2026-08-09T08:00:00", timezone: "America/New_York", utc_offset_minutes: -240 }, specimen_type: "Serum", laboratory_name: "Synthetic laboratory", source_type: "web", confirmation_state: "direct" };

function renderPage(results: unknown[]): void {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(results), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><AuthContext.Provider value={{ status: "authenticated", session, signIn: vi.fn(), signOut: vi.fn() }}><LabsPage /></AuthContext.Provider></MemoryRouter></QueryClientProvider>);
}

describe("Labs page", () => {
  it("separates source and derived data and provides an equivalent trend table", async () => {
    renderPage([base, { ...base, id: "33333333-3333-4333-8333-333333333333", specimen_time: { ...base.specimen_time, occurred_at: "2026-08-10T12:00:00Z", local_time: "2026-08-10T08:00:00" }, original_value: "11", normalized_value: "303.6000000000" }, { ...base, id: "44444444-4444-4444-8444-444444444444", specimen_type: "Saliva", original_unit: "unsupported", normalized_value: null, normalized_unit: null, normalization_method: null }]);
    expect(await screen.findByRole("heading", { name: "Cortisol — Serum" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Source result" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Derived result" })).toBeVisible();
    expect(screen.getByText("Not derived—original preserved")).toBeVisible();
    expect(screen.getByRole("region", { name: "Cortisol — Serum data table" })).toBeInTheDocument();
    expect(screen.getByText(/does not diagnose, interpret cortisol/)).toBeVisible();
  });
});
