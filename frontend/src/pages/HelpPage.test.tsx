import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { AuthContext, type AuthContextValue } from "../auth/context";
import helpContent from "../helpContent.json";

const auth: AuthContextValue = {
  status: "authenticated",
  session: {
    csrfToken: "synthetic-csrf",
    user: {
      email: "owner@example.test",
      displayName: "Synthetic Owner",
      defaultTimezone: "America/New_York",
    },
  },
  signIn: vi.fn(),
  signOut: vi.fn(),
};

function renderHelp(): void {
  render(<AuthContext.Provider value={auth}><MemoryRouter initialEntries={["/help"]}><App /></MemoryRouter></AuthContext.Provider>);
}

describe("Help page", () => {
  it("is authenticated primary navigation with prominent safety and category boundaries", async () => {
    renderHelp();

    const pageTitle = await screen.findByRole("heading", { name: "Help", level: 1 });
    expect(pageTitle).toBeVisible();
    expect(pageTitle).toHaveClass("page-title");
    expect(pageTitle).toHaveFocus();
    expect(screen.getByRole("heading", { name: "AI for AI" })).toBeVisible();
    expect(screen.getByText(/Artificial Intelligence for Adrenal Insufficiency/)).toBeVisible();
    expect(screen.getByText(/not diagnosis, emergency care, or medication advice/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Help" })).toHaveClass("active");
    const safety = screen.getByRole("heading", { name: "HealthCurve.ai is not emergency care or dosing advice" }).closest("aside");
    if (safety === null) throw new Error("emergency safety region missing");
    expect(within(safety).getByText(/contact local emergency services/)).toBeVisible();
    expect(within(safety).getByText(/does not decide whether, when, or how much/)).toBeVisible();
    expect(screen.getByRole("navigation", { name: "Help topics" })).toBeVisible();
    expect(document.querySelector('[data-category="fact"]')).toBeVisible();
    expect(document.querySelector('[data-category="plan"]')).toBeVisible();
    expect(document.querySelector('[data-category="ai"]')).toBeVisible();
  });

  it("documents every manifest command with a copyable example and confirmation state", async () => {
    const writeText = vi.fn<(value: string) => Promise<void>>().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    renderHelp();
    await screen.findByRole("heading", { name: "Help", level: 1 });

    for (const item of helpContent.telegramCommands) {
      const heading = screen.getByRole("heading", { name: item.command });
      const card = heading.closest("article");
      if (card === null) throw new Error(`${item.command} help card missing`);
      expect(within(card).getByText(item.result)).toBeVisible();
      expect(within(card).getByText(item.confirmation)).toBeVisible();
      expect(within(card).getByLabelText(`${item.command} example`)).toHaveTextContent(item.example);
    }

    const doseCard = screen.getByRole("heading", { name: "/dose" }).closest("article");
    if (doseCard === null) throw new Error("/dose help card missing");
    const copy = within(doseCard).getByRole("button", { name: "Copy /dose example" });
    copy.focus();
    fireEvent.click(copy);
    await waitFor(() => { expect(writeText).toHaveBeenCalledWith("/dose 10 hydrocortisone 07:05"); });
    expect(within(doseCard).getByRole("status")).toHaveTextContent("Copied");
  });

  it("labels implemented entry capabilities while documenting backlog capture", async () => {
    renderHelp();
    await screen.findByRole("heading", { name: "Help", level: 1 });

    expect(screen.getByRole("heading", { name: "Record a diary entry or life event" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Open record a diary entry or life event" })).toHaveAttribute("href", "/symptoms-diary");
    expect(screen.getByText("Authenticated API")).toBeVisible();
    expect(screen.getByText("Labs web page")).toBeVisible();
    expect(screen.getByText(/original PDF remains attachment-only/i)).toBeVisible();
    expect(screen.getByText(/permanent deletion first shows the exact linked drafts/i)).toBeVisible();
    expect(screen.queryByText(/fact-confirmation UI is planned/i)).not.toBeInTheDocument();
    expect(screen.getByText("Settings, Health data, and API")).toBeVisible();
    expect(screen.getByText(/local one-time Garmin connection/)).toBeVisible();
    expect(screen.queryByText(/automatic provider sync is not implemented/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "/beads-add" })).toBeVisible();
    expect(screen.getByText(/no agent, shell command, or implementation starts/i)).toBeVisible();
    expect(screen.getByText(/never falls back to copying the directive/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Open record a scheduled dose from today" })).toHaveAttribute("href", "/today");
    expect(screen.getByRole("link", { name: "Open emergency page" })).toHaveAttribute("href", "/emergency");
  });

  it("reports the configured offsite backup and proven recovery state", async () => {
    renderHelp();
    await screen.findByRole("heading", { name: "Help", level: 1 });

    expect(screen.getByRole("heading", { name: "Backup and recovery status" })).toBeVisible();
    expect(screen.getByText(/nightly encrypted local and Google Drive backup copies are configured/i)).toBeVisible();
    expect(screen.getByText(/first isolated restore drill passed/i)).toBeVisible();
    expect(screen.queryByText(/still requires an offsite copy/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Clearly not available yet/i)).not.toBeInTheDocument();
  });
});
