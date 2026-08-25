import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { MemoryRouter } from "react-router-dom";

import * as api from "../api/client";
import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { ChatPage } from "./ChatPage";

vi.mock("../api/client", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api/client")>();
  return {
    ...original,
    getChatConversations: vi.fn(),
    getChatMessages: vi.fn(),
    getChatMessageStaleness: vi.fn(),
    createChatConversation: vi.fn(),
    updateChatConversation: vi.fn(),
    deleteChatConversation: vi.fn(),
    sendChatMessage: vi.fn(),
    cancelChatMessage: vi.fn(),
  };
});

const conversation: api.ChatConversation = {
  category: "ai",
  id: "00000000-0000-4000-8000-000000000001",
  title: "Review yesterday",
  include_sensitive_text: false,
  created_at: "2026-08-16T12:00:00Z",
  updated_at: "2026-08-16T12:01:00Z",
  last_message_at: "2026-08-16T12:01:00Z",
  retention_expires_at: null,
};

function message(overrides: Partial<api.ChatMessage>): api.ChatMessage {
  return {
    category: "ai",
    content_category: "ai_generated",
    id: "00000000-0000-4000-8000-000000000003",
    conversation_id: conversation.id,
    role: "assistant",
    state: "completed",
    body: "Stress rose after noon in the available observations. This is an association, not a cause.",
    sequence: 2,
    generated_at: "2026-08-16T12:01:00Z",
    model_name: "synthetic-local-model",
    model_digest: "synthetic-digest",
    prompt_version: "chat-v1",
    schema_version: "chat-answer-v1",
    tool_versions: { daily: "v1" },
    source_manifest: [{ tool_name: "daily_healthcurve", local_date: "2026-08-15" }],
    source_scope: { local_date: "2026-08-15" },
    source_fingerprint: "synthetic-fingerprint",
    error_code: null,
    created_at: "2026-08-16T12:00:30Z",
    updated_at: "2026-08-16T12:01:00Z",
    ...overrides,
  };
}

function renderChat(messages: api.ChatMessage[]): void {
  vi.mocked(api.getChatConversations).mockResolvedValue({ items: [conversation], page: { page: 1, page_size: 50, total_items: 1, total_pages: 1 } });
  vi.mocked(api.getChatMessages).mockResolvedValue({ items: messages, page: { page: 1, page_size: 100, total_items: messages.length, total_pages: 1 } });
  vi.mocked(api.getChatMessageStaleness).mockResolvedValue({ status: "fresh", stale: false, checked_at: "2026-08-16T12:02:00Z" });
  render(<HealthCurveProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter initialEntries={["/chat"]}><ChatPage /></MemoryRouter></QueryClientProvider></HealthCurveProvider>);
}

describe("HealthCurve Chat page", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("shows persisted owner and AI messages with expandable data provenance", async () => {
    renderChat([
      message({ id: "00000000-0000-4000-8000-000000000002", role: "user", content_category: "owner_authored", state: "accepted", body: "What happened yesterday?", sequence: 1, generated_at: null, model_name: null, model_digest: null, prompt_version: null, schema_version: null, tool_versions: null, source_manifest: null, source_scope: null, source_fingerprint: null }),
      message({}),
    ]);
    expect(await screen.findByRole("heading", { name: "Chat", level: 1 })).toBeVisible();
    expect(await screen.findByText("What happened yesterday?")).toBeVisible();
    expect(screen.getByText(/Stress rose after noon/)).toBeVisible();
    const details = screen.getByText("Data used and AI details");
    await userEvent.click(details);
    expect(screen.getByText("synthetic-local-model")).toBeVisible();
    expect(screen.getByText("daily healthcurve")).toBeVisible();
    expect(screen.getByText(/kept separate from recorded facts/)).toBeVisible();
    document.documentElement.lang = "en";
    const audit = await axe.run(document, {
      resultTypes: ["violations"],
      rules: { "color-contrast": { enabled: false } },
    });
    expect(
      audit.violations
        .filter((violation) => violation.impact === "critical" || violation.impact === "serious")
        .map((violation) => violation.id),
    ).toEqual([]);
  });

  it("sends a natural-language question and preserves the sensitive-text opt-in", async () => {
    renderChat([]);
    vi.mocked(api.sendChatMessage).mockResolvedValue(message({ role: "user", content_category: "owner_authored", state: "accepted", body: "Compare stress and symptoms yesterday", sequence: 1 }));
    vi.mocked(api.updateChatConversation).mockResolvedValue({ ...conversation, include_sensitive_text: true });
    const user = userEvent.setup();
    const composer = await screen.findByLabelText("Message HealthCurve AI");
    await user.type(composer, "Compare stress and symptoms yesterday");
    await user.keyboard("[Enter]");
    await waitFor(() => { expect(api.sendChatMessage).toHaveBeenCalledWith(conversation.id, "Compare stress and symptoms yesterday", expect.any(String)); });
    await user.click(screen.getByLabelText("Include sensitive diary and life-event text"));
    await waitFor(() => { expect(api.updateChatConversation).toHaveBeenCalledWith(conversation.id, { include_sensitive_text: true }); });
  });

  it("collapses and restores the entire New chat and history rail", async () => {
    renderChat([]);
    const user = userEvent.setup();
    const rail = await screen.findByRole("complementary", { name: "Chat conversations" });
    expect(rail).toBeVisible();
    expect(within(rail).getByRole("button", { name: "New chat" })).toBeVisible();

    const hide = screen.getByRole("button", { name: "Hide New chat and history" });
    expect(hide).toHaveAttribute("aria-expanded", "true");
    await user.click(hide);
    expect(screen.queryByRole("complementary", { name: "Chat conversations" })).not.toBeInTheDocument();

    const show = screen.getByRole("button", { name: "Show New chat and history" });
    expect(show).toHaveAttribute("aria-expanded", "false");
    await user.click(show);
    const restoredRail = await screen.findByRole("complementary", { name: "Chat conversations" });
    expect(restoredRail).toBeVisible();
    expect(within(restoredRail).getByRole("button", { name: "New chat" })).toBeVisible();
  });

  it("condenses completed earlier turns while keeping the latest turn open", async () => {
    renderChat([
      message({ id: "00000000-0000-4000-8000-000000000010", role: "user", content_category: "owner_authored", state: "accepted", body: "What happened before my headache?", sequence: 1, generated_at: null }),
      message({ id: "00000000-0000-4000-8000-000000000011", body: "Earlier contextual answer", sequence: 2 }),
      message({ id: "00000000-0000-4000-8000-000000000012", role: "user", content_category: "owner_authored", state: "accepted", body: "Was the data unusual?", sequence: 3, generated_at: null }),
      message({ id: "00000000-0000-4000-8000-000000000013", body: "Latest contextual answer", sequence: 4 }),
    ]);
    const user = userEvent.setup();

    expect(await screen.findByText("Latest contextual answer")).toBeVisible();
    expect(screen.getByText("Earlier contextual answer")).not.toBeVisible();
    const earlierToggle = screen.getByRole("button", { name: /What happened before my headache.*Expand/ });
    expect(earlierToggle).toHaveAttribute("aria-expanded", "false");

    await user.click(earlierToggle);
    expect(screen.getByText("Earlier contextual answer")).toBeVisible();
    expect(earlierToggle).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("button", { name: "Collapse previous turns" }));
    expect(screen.getByText("Earlier contextual answer")).not.toBeVisible();
    await user.click(screen.getByRole("button", { name: "Expand previous turns" }));
    expect(screen.getByText("Earlier contextual answer")).toBeVisible();
  });

  it("makes durable failures visible and offers retry", async () => {
    const userMessage = message({ id: "00000000-0000-4000-8000-000000000004", role: "user", content_category: "owner_authored", state: "accepted", body: "Summarize my day", sequence: 1, generated_at: null });
    renderChat([userMessage, message({ id: "00000000-0000-4000-8000-000000000005", state: "unavailable", body: null, error_code: "model_unavailable" })]);
    vi.mocked(api.sendChatMessage).mockResolvedValue(userMessage);
    expect(await screen.findByText(/private model is unavailable/i)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => { expect(api.sendChatMessage).toHaveBeenCalledWith(conversation.id, "Summarize my day", expect.any(String)); });
  });
});
