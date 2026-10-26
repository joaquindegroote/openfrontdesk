"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Conversation, type ConversationDetail } from "@/lib/api";
import { humanize, relativeTime } from "@/lib/format";
import { ConversationStatusBadge } from "./badges";
import { Composer } from "./composer";
import { MessageList } from "./message-list";

type Filter = "handed_off" | "all";

function customerLabel(conversation: Conversation): string {
  return conversation.customer_name ?? conversation.customer_phone ?? "Web visitor";
}

export function Inbox() {
  const [filter, setFilter] = useState<Filter>("handed_off");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [author, setAuthor] = useState("Dana");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.conversations(filter === "all" ? undefined : "handed_off");
      setConversations(list);
      setDetail(selected ? await api.conversation(selected) : null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [filter, selected]);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(refresh, 3000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh]);

  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const conversation = detail?.conversation;

  return (
    <div className="grid flex-1 gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
      <section className="flex min-h-[320px] flex-col overflow-hidden rounded-xl border border-border bg-surface lg:h-[calc(100vh-8.5rem)]">
        <header className="flex items-center gap-2 border-b border-border px-4 py-3">
          <h1 className="font-semibold">Staff inbox</h1>
          <div className="ml-auto flex rounded-lg bg-surface-muted p-0.5 text-xs">
            {(["handed_off", "all"] as const).map((value) => (
              <button
                key={value}
                onClick={() => setFilter(value)}
                className={`rounded-md px-2.5 py-1 ${
                  filter === value ? "bg-surface font-medium shadow-sm" : "text-muted"
                }`}
              >
                {value === "handed_off" ? "Needs staff" : "All"}
              </button>
            ))}
          </div>
        </header>
        <ul className="flex-1 divide-y divide-border overflow-y-auto">
          {conversations.length === 0 && (
            <li className="p-4 text-sm text-muted">
              {filter === "handed_off"
                ? "Nothing needs a person right now. Conversations the AI hands off land here."
                : "No conversations yet. Start one from the chat demo."}
            </li>
          )}
          {conversations.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => setSelected(c.id)}
                className={`flex w-full flex-col gap-1 px-4 py-3 text-left text-sm hover:bg-surface-muted ${
                  c.id === selected ? "bg-accent-soft" : ""
                }`}
              >
                <span className="flex items-center gap-2">
                  <span className="font-medium">{customerLabel(c)}</span>
                  <span className="ml-auto text-xs text-muted">{relativeTime(c.updated_at)}</span>
                </span>
                <span className="flex items-center gap-2 text-xs text-muted">
                  <ConversationStatusBadge status={c.status} />
                  {c.handoff_reason && humanize(c.handoff_reason)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex min-h-[600px] flex-col overflow-hidden rounded-xl border border-border bg-surface lg:h-[calc(100vh-8.5rem)]">
        {!conversation ? (
          <div className="grid flex-1 place-items-center p-8 text-sm text-muted">
            {error ?? "Select a conversation to read it and reply as the front desk."}
          </div>
        ) : (
          <>
            <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
              <div>
                <h2 className="font-semibold">{customerLabel(conversation)}</h2>
                <p className="text-xs text-muted">
                  {conversation.channel} · started {relativeTime(conversation.created_at)}
                </p>
              </div>
              <ConversationStatusBadge status={conversation.status} />
              <div className="ml-auto">
                {conversation.status === "handed_off" ? (
                  <button
                    onClick={() => act(() => api.resume(conversation.id))}
                    className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white"
                  >
                    Hand back to AI
                  </button>
                ) : (
                  <button
                    onClick={() => act(() => api.takeOver(conversation.id))}
                    className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-surface-muted"
                  >
                    Take over
                  </button>
                )}
              </div>
            </header>

            {conversation.status === "handed_off" && (
              <div className="border-b border-warn/30 bg-warn-soft px-4 py-3 text-sm">
                <p className="font-medium text-warn">{humanize(conversation.handoff_reason)}</p>
                {conversation.handoff_summary && (
                  <p className="mt-1 text-foreground">{conversation.handoff_summary}</p>
                )}
              </div>
            )}

            <MessageList messages={detail.messages} assistantName="Ava" viewer="staff" />

            {error && (
              <p className="border-t border-danger/30 bg-danger-soft px-4 py-2 text-xs text-danger">
                {error}
              </p>
            )}
            <div className="flex items-center gap-2 border-t border-border px-3 pt-3 text-xs text-muted">
              Replying as
              <input
                value={author}
                onChange={(event) => setAuthor(event.target.value)}
                className="w-32 rounded border border-border bg-surface px-2 py-1 text-foreground"
              />
              {conversation.status === "active" && <span>(sending takes over from the AI)</span>}
            </div>
            <Composer
              placeholder="Reply to the customer…"
              onSend={(text) =>
                act(() => api.sendStaffMessage(conversation.id, text, author || "Front desk"))
              }
            />
          </>
        )}
      </section>
    </div>
  );
}
