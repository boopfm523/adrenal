import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Group, Loader, Textarea } from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  cancelChatMessage,
  createChatConversation,
  deleteChatConversation,
  getChatConversations,
  getChatMessages,
  getChatMessageStaleness,
  sendChatMessage,
  updateChatConversation,
  type ChatConversation,
  type ChatMessage,
} from "../api/client";
import { Page } from "../components/Page";

const ACTIVE_STATES = new Set<ChatMessage["state"]>(["queued", "planning", "reading", "generating"]);
const FAILURE_STATES = new Set<ChatMessage["state"]>(["cancelled", "unavailable", "timed_out", "invalid", "failed"]);

const statusText: Record<ChatMessage["state"], string> = {
  accepted: "Accepted",
  queued: "Waiting for the private model…",
  planning: "Understanding your question…",
  reading: "Reading the relevant HealthCurve data…",
  generating: "Preparing an answer…",
  completed: "Completed",
  cancelled: "Response cancelled.",
  unavailable: "The private model is unavailable right now. Your message is still saved.",
  timed_out: "The private model did not finish in time. Your message is still saved.",
  invalid: "The private model returned an answer HealthCurve could not safely validate.",
  failed: "HealthCurve could not complete this answer. Your records were not changed.",
};

function newClientMessageId(): string {
  return typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `web-${Date.now().toString()}`;
}

function formatConversationDate(value: string | null): string {
  if (value === null) return "No messages yet";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function sourceLabel(source: Record<string, unknown>, index: number): string {
  for (const key of ["label", "tool", "tool_name", "domain", "record_type"]) {
    const value = source[key];
    if (typeof value === "string" && value.trim() !== "") return value.replaceAll("_", " ");
  }
  return `Data source ${String(index + 1)}`;
}

function StalenessNotice({ message }: { message: ChatMessage }): React.JSX.Element | null {
  const query = useQuery({
    queryKey: ["chat-staleness", message.id, message.source_fingerprint],
    queryFn: () => getChatMessageStaleness(message.id),
    enabled: message.state === "completed" && message.source_fingerprint !== null,
    staleTime: 30_000,
  });
  if (query.data?.stale !== true) return null;
  return <p className="chat-stale-note" role="status">Data used for this answer has changed. Ask again to include the latest recorded facts.</p>;
}

interface MessageCardProps {
  message: ChatMessage;
  priorUserMessage: ChatMessage | undefined;
  onCancel: (id: string) => void;
  onRetry: (body: string) => void;
  cancelling: boolean;
  retrying: boolean;
}

interface ConversationTurn {
  id: string;
  messages: ChatMessage[];
  user: ChatMessage | undefined;
  assistant: ChatMessage | undefined;
}

function groupConversationTurns(messages: ChatMessage[]): ConversationTurn[] {
  const turns: ConversationTurn[] = [];
  for (const message of messages) {
    const current = turns.at(-1);
    if (message.role === "user" || current === undefined || current.assistant !== undefined) {
      turns.push({
        id: message.id,
        messages: [message],
        user: message.role === "user" ? message : undefined,
        assistant: message.role === "assistant" ? message : undefined,
      });
      continue;
    }
    current.messages.push(message);
    current.assistant = message;
  }
  return turns;
}

function turnSummary(turn: ConversationTurn): string {
  const trimmedBody = turn.user?.body?.trim();
  const body = trimmedBody === undefined || trimmedBody === "" ? "Earlier response" : trimmedBody;
  return body.length > 96 ? `${body.slice(0, 93)}…` : body;
}

function MessageCard({ message, priorUserMessage, onCancel, onRetry, cancelling, retrying }: MessageCardProps): React.JSX.Element {
  const isAssistant = message.role === "assistant";
  const active = ACTIVE_STATES.has(message.state);
  const failed = FAILURE_STATES.has(message.state);
  return (
    <article className={`chat-message chat-message--${message.role}`} aria-label={isAssistant ? "HealthCurve AI response" : "Your message"}>
      <div className="chat-message__label">{isAssistant ? "HealthCurve AI" : "You"}</div>
      {message.body !== null ? <p className="chat-message__body">{message.body}</p> : null}
      {isAssistant && active ? <div className="chat-message__working" role="status"><Loader size="xs" aria-hidden="true" /> {statusText[message.state]}</div> : null}
      {isAssistant && failed ? <Alert color="grape" variant="light" role="alert">{statusText[message.state]}{message.error_code === null ? null : <span className="chat-error-code"> Reference: {message.error_code}</span>}</Alert> : null}
      {isAssistant && active ? <Button variant="outline" size="xs" loading={cancelling} onClick={() => { onCancel(message.id); }}>Cancel response</Button> : null}
      {isAssistant && failed && priorUserMessage?.body != null ? <Button variant="outline" size="xs" loading={retrying} onClick={() => { onRetry(priorUserMessage.body ?? ""); }}>Try again</Button> : null}
      {isAssistant ? <StalenessNotice message={message} /> : null}
      {isAssistant && message.state === "completed" ? (
        <details className="chat-provenance">
          <summary>Data used and AI details</summary>
          <div>
            <p><strong>Private model:</strong> {message.model_name ?? "Not recorded"}</p>
            <p><strong>Generated:</strong> {message.generated_at === null ? "Not recorded" : formatConversationDate(message.generated_at)}</p>
            <p><strong>Data used:</strong></p>
            {message.source_manifest === null || message.source_manifest.length === 0
              ? <p>No source records were needed for this answer.</p>
              : <ul>{message.source_manifest.map((source, index) => <li key={`${message.id}-${String(index)}`}>{sourceLabel(source, index)}</li>)}</ul>}
            <p className="chat-provenance__boundary">AI-generated interpretation is kept separate from recorded facts and physician-approved plans.</p>
          </div>
        </details>
      ) : null}
    </article>
  );
}

function ConversationList({ conversations, selectedId, onSelect, onCreate, onDelete, creating, deletingId }: { conversations: ChatConversation[]; selectedId: string | null; onSelect: (id: string) => void; onCreate: () => void; onDelete: (id: string) => void; creating: boolean; deletingId: string | null }): React.JSX.Element {
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  return (
    <aside id="chat-conversation-rail" className="chat-conversations" aria-label="Chat conversations">
      <Button fullWidth onClick={onCreate} loading={creating}>New chat</Button>
      <h2 className="chat-history-heading">Conversation history ({conversations.length})</h2>
      <div className="chat-conversation-list">
        {conversations.length === 0 ? <p className="chat-empty-copy">No conversations yet.</p> : conversations.map((conversation) => (
          <div className={`chat-conversation${conversation.id === selectedId ? " chat-conversation--active" : ""}`} key={conversation.id}>
            <button type="button" className="chat-conversation__select" aria-pressed={conversation.id === selectedId} onClick={() => { onSelect(conversation.id); }}>
              <span>{conversation.title}</span><small>{formatConversationDate(conversation.last_message_at)}</small>
            </button>
            {confirmDeleteId === conversation.id ? <div className="chat-conversation__confirm"><span>Delete?</span><button type="button" disabled={deletingId === conversation.id} onClick={() => { onDelete(conversation.id); }}>Yes</button><button type="button" onClick={() => { setConfirmDeleteId(null); }}>No</button></div> : <button type="button" className="chat-conversation__delete" aria-label={`Delete ${conversation.title}`} onClick={() => { setConfirmDeleteId(conversation.id); }}>Delete</button>}
          </div>
        ))}
      </div>
    </aside>
  );
}

export function ChatPage(): React.JSX.Element {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const conversations = useQuery({ queryKey: ["chat-conversations"], queryFn: () => getChatConversations() });
  const effectiveSelectedId = selectedId ?? conversations.data?.items[0]?.id ?? null;
  const selected = conversations.data?.items.find((item) => item.id === effectiveSelectedId) ?? null;

  const messages = useQuery({
    queryKey: ["chat-messages", effectiveSelectedId],
    queryFn: () => getChatMessages(effectiveSelectedId ?? ""),
    enabled: effectiveSelectedId !== null,
    refetchInterval: (query) => query.state.data?.items.some((message) => ACTIVE_STATES.has(message.state)) === true ? 1500 : false,
  });
  const orderedMessages = useMemo(() => [...(messages.data?.items ?? [])].sort((a, b) => a.sequence - b.sequence), [messages.data]);
  const turns = useMemo(() => groupConversationTurns(orderedMessages), [orderedMessages]);
  const priorTurns = turns.slice(0, -1);
  const [expandedPriorTurnIds, setExpandedPriorTurnIds] = useState<Set<string>>(() => new Set());
  const expandedPriorTurnCount = priorTurns.filter((turn) => expandedPriorTurnIds.has(turn.id)).length;
  const activeAssistant = [...orderedMessages].reverse().find((message) => message.role === "assistant" && ACTIVE_STATES.has(message.state));
  const latestStateKey = orderedMessages.map((message) => `${message.id}:${message.state}`).join("|");
  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === "function") endRef.current.scrollIntoView({ block: "nearest" });
  }, [latestStateKey]);

  const createConversation = useMutation({ mutationFn: () => createChatConversation(), onSuccess: async (conversation) => { setSelectedId(conversation.id); await queryClient.invalidateQueries({ queryKey: ["chat-conversations"] }); } });
  const removeConversation = useMutation({ mutationFn: (id: string) => deleteChatConversation(id), onSuccess: async (_data, id) => { if (effectiveSelectedId === id) setSelectedId(null); await queryClient.invalidateQueries({ queryKey: ["chat-conversations"] }); } });
  const updateConversation = useMutation({ mutationFn: ({ id, includeSensitiveText }: { id: string; includeSensitiveText: boolean }) => updateChatConversation(id, { include_sensitive_text: includeSensitiveText }), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["chat-conversations"] }); } });
  const send = useMutation({
    mutationFn: ({ conversationId, body }: { conversationId: string; body: string }) => sendChatMessage(conversationId, body, newClientMessageId()),
    onSuccess: async (_message, variables) => {
      setDraft("");
      if (selected?.title === "New conversation") void updateChatConversation(variables.conversationId, { title: variables.body.slice(0, 72) });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["chat-messages", variables.conversationId] }), queryClient.invalidateQueries({ queryKey: ["chat-conversations"] })]);
    },
  });
  const cancel = useMutation({ mutationFn: (id: string) => cancelChatMessage(id), onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["chat-messages", effectiveSelectedId] }); } });

  function submit(body: string): void {
    const trimmed = body.trim();
    if (effectiveSelectedId === null || trimmed === "" || activeAssistant !== undefined) return;
    send.mutate({ conversationId: effectiveSelectedId, body: trimmed });
  }

  return (
    <Page title="Chat" description="Ask your private HealthCurve AI questions about your recorded data. Answers are exploratory, not diagnoses or dosing advice.">
      <Alert color="blue" variant="light" title="Private, read-only analysis" role="note">The chatbot can read only your approved HealthCurve data tools. It cannot change recorded facts or plans, and no health text is sent to a cloud AI service.</Alert>
      <div className="chat-history-controls">
        <Button
          variant="outline"
          aria-controls="chat-conversation-rail"
          aria-expanded={!historyCollapsed}
          onClick={() => { setHistoryCollapsed((collapsed) => !collapsed); }}
        >
          {historyCollapsed ? "Show New chat and history" : "Hide New chat and history"}
        </Button>
      </div>
      <div className={`chat-layout${historyCollapsed ? " chat-layout--history-collapsed" : ""}`}>
        {historyCollapsed ? null : <ConversationList conversations={conversations.data?.items ?? []} selectedId={effectiveSelectedId} onSelect={setSelectedId} onCreate={() => { createConversation.mutate(); }} onDelete={(id) => { removeConversation.mutate(id); }} creating={createConversation.isPending} deletingId={removeConversation.isPending ? removeConversation.variables : null} />}
        <section className="chat-panel" aria-label="Current conversation">
          {conversations.isError ? <Alert color="red" role="alert">Conversations could not be loaded.</Alert> : null}
          {selected === null ? <div className="chat-welcome"><h2>Start a conversation</h2><p>Ask about a day, a trend, recorded symptoms, doses, episodes, sleep, or Garmin observations.</p><Button onClick={() => { createConversation.mutate(); }} loading={createConversation.isPending}>New chat</Button></div> : (
            <>
              <header className="chat-panel__header"><div><h2>{selected.title}</h2><span className="category-label">AI conversation</span></div><Checkbox checked={selected.include_sensitive_text} label="Include sensitive diary and life-event text" onChange={(event) => { updateConversation.mutate({ id: selected.id, includeSensitiveText: event.currentTarget.checked }); }} /></header>
              <div className="chat-messages" aria-live="polite" aria-busy={activeAssistant !== undefined}>
                {messages.isPending ? <p role="status">Loading conversation…</p> : null}
                {messages.isError ? <Alert color="red" role="alert">This conversation could not be loaded.</Alert> : null}
                {orderedMessages.length === 0 && !messages.isPending ? <div className="chat-welcome"><h3>What would you like to understand?</h3><p>For example: “What was happening around my symptoms yesterday?” or “Compare my stress and heart rate over the last week.”</p></div> : null}
                {priorTurns.length > 0 ? (
                  <div className="chat-turn-controls">
                    <Button
                      variant="subtle"
                      size="xs"
                      onClick={() => {
                        setExpandedPriorTurnIds((current) => {
                          const next = new Set(current);
                          for (const turn of priorTurns) {
                            if (expandedPriorTurnCount === priorTurns.length) next.delete(turn.id);
                            else next.add(turn.id);
                          }
                          return next;
                        });
                      }}
                    >
                      {expandedPriorTurnCount === priorTurns.length ? "Collapse previous turns" : "Expand previous turns"}
                    </Button>
                    <span>{priorTurns.length} earlier {priorTurns.length === 1 ? "turn" : "turns"}</span>
                  </div>
                ) : null}
                {turns.map((turn, turnIndex) => {
                  const isLatest = turnIndex === turns.length - 1;
                  const expanded = isLatest || expandedPriorTurnIds.has(turn.id);
                  const panelId = `chat-turn-${turn.id}`;
                  return (
                    <section className={`chat-turn${expanded ? "" : " chat-turn--collapsed"}`} key={turn.id} aria-label={isLatest ? "Latest conversation turn" : "Earlier conversation turn"}>
                      {isLatest ? null : (
                        <button
                          type="button"
                          className="chat-turn__toggle"
                          aria-expanded={expanded}
                          aria-controls={panelId}
                          onClick={() => {
                            setExpandedPriorTurnIds((current) => {
                              const next = new Set(current);
                              if (next.has(turn.id)) next.delete(turn.id);
                              else next.add(turn.id);
                              return next;
                            });
                          }}
                        >
                          <span>{turnSummary(turn)}</span>
                          <small>{expanded ? "Collapse" : "Expand"}</small>
                        </button>
                      )}
                      <div id={panelId} hidden={!expanded} className="chat-turn__messages">
                        {turn.messages.map((message) => {
                          const priorUser = message.role === "assistant" ? turn.user : undefined;
                          return <MessageCard key={message.id} message={message} priorUserMessage={priorUser} onCancel={(id) => { cancel.mutate(id); }} onRetry={submit} cancelling={cancel.isPending && cancel.variables === message.id} retrying={send.isPending} />;
                        })}
                      </div>
                    </section>
                  );
                })}
                <div ref={endRef} />
              </div>
              <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); submit(draft); }}>
                <Textarea aria-label="Message HealthCurve AI" placeholder="Ask about your HealthCurve data…" rows={3} value={draft} disabled={activeAssistant !== undefined} onChange={(event) => { setDraft(event.currentTarget.value); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); submit(draft); } }} />
                <Group justify="space-between"><span className="chat-composer__hint">Enter to send · Shift+Enter for a new line</span><Button type="submit" loading={send.isPending} disabled={draft.trim() === "" || activeAssistant !== undefined}>Send</Button></Group>
                {send.isError ? <Alert color="red" role="alert">Your message could not be sent. It was not added to the conversation.</Alert> : null}
              </form>
            </>
          )}
        </section>
      </div>
    </Page>
  );
}
