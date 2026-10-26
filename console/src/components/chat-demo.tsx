"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Business, type ConversationDetail, type Message } from "@/lib/api";
import { humanize } from "@/lib/format";
import { ConversationStatusBadge } from "./badges";
import { Composer } from "./composer";
import { EventTimeline } from "./event-timeline";
import { MessageList } from "./message-list";

// Seeded patients in the mock clinic. Starting a chat "from" a phone number simulates a channel
// (WhatsApp, SMS, a call) where the number is known.
const PERSONAS = [
  { id: "new", name: "New visitor", note: "No phone on file", phone: null },
  { id: "emily", name: "Emily Johnson", note: "Cleaning soon: late-cancel fee", phone: "+15550100101" },
  { id: "carlos", name: "Carlos Rivera", note: "Check-up in a few days", phone: "+15550100102" },
  { id: "priya", name: "Priya Shah", note: "Returning patient", phone: "+15550100103" },
] as const;

type Persona = (typeof PERSONAS)[number];

const SUGGESTIONS = [
  "I'd like a cleaning next week",
  "I need to cancel my appointment",
  "Do you take insurance?",
  "Can I talk to a person?",
];

export function ChatDemo() {
  const [business, setBusiness] = useState<Business | null>(null);
  const [persona, setPersona] = useState<Persona>(PERSONAS[1]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [optimistic, setOptimistic] = useState<Message | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.business().then(setBusiness, (e: Error) => setError(e.message));
  }, []);

  const status = detail?.conversation.status;
  // While staff own the conversation, poll so their replies appear here.
  useEffect(() => {
    if (!conversationId || status !== "handed_off") return;
    const timer = setInterval(() => {
      api.conversation(conversationId).then(setDetail, () => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [conversationId, status]);

  async function send(text: string) {
    setError(null);
    setSending(true);
    setOptimistic({
      id: -1,
      role: "customer",
      text,
      author: null,
      turn_id: null,
      created_at: new Date().toISOString(),
    });
    let id = conversationId;
    try {
      if (!id) {
        id = (await api.startConversation(persona.phone)).id;
        setConversationId(id);
      }
      await api.sendMessage(id, text);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
    if (id) {
      await api.conversation(id).then(setDetail, () => {});
    }
    setOptimistic(null);
    setSending(false);
  }

  function restart(next: Persona = persona) {
    setPersona(next);
    setConversationId(null);
    setDetail(null);
    setError(null);
  }

  const assistantName = business?.assistant_name ?? "Ava";
  const messages = [...(detail?.messages ?? []), ...(optimistic ? [optimistic] : [])];

  return (
    <div className="grid flex-1 gap-6 lg:grid-cols-[minmax(0,1fr)_420px]">
      <section className="flex min-h-[600px] flex-col overflow-hidden rounded-xl border border-border bg-surface lg:h-[calc(100vh-8.5rem)]">
        <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
          <div>
            <h1 className="font-semibold">{business?.name ?? "Loading…"}</h1>
            <p className="text-xs text-muted">Chat with {assistantName}, the AI front desk</p>
          </div>
          {status && <ConversationStatusBadge status={status} />}
          <button
            onClick={() => restart()}
            className="ml-auto rounded-md border border-border px-3 py-1.5 text-xs hover:bg-surface-muted"
          >
            New chat
          </button>
        </header>

        <div className="flex flex-wrap gap-2 border-b border-border px-4 py-3">
          <span className="w-full text-[11px] font-medium uppercase tracking-wide text-muted">
            Chat as
          </span>
          {PERSONAS.map((p) => (
            <button
              key={p.id}
              onClick={() => restart(p)}
              className={`rounded-lg border px-3 py-1.5 text-left text-xs transition-colors ${
                p.id === persona.id
                  ? "border-accent bg-accent-soft"
                  : "border-border hover:bg-surface-muted"
              }`}
            >
              <span className="block font-medium">{p.name}</span>
              <span className="text-muted">{p.note}</span>
            </button>
          ))}
        </div>

        {status === "handed_off" && (
          <div className="border-b border-warn/30 bg-warn-soft px-4 py-2 text-xs text-warn">
            Handed off to staff ({humanize(detail?.conversation.handoff_reason ?? null)}). Reply as
            the front desk from the{" "}
            <Link href="/inbox" className="font-medium underline">
              staff inbox
            </Link>
            .
          </div>
        )}

        <MessageList
          messages={messages}
          assistantName={assistantName}
          viewer="customer"
          pending={sending ? `${assistantName} is working on it…` : null}
          empty={
            <div className="flex max-w-sm flex-col items-center gap-3">
              <p>
                Ask {assistantName} anything a dental front desk handles. Try one of these:
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => send(suggestion)}
                    className="rounded-full border border-border px-3 py-1 text-xs text-foreground hover:bg-surface-muted"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          }
        />

        {error && (
          <p className="border-t border-danger/30 bg-danger-soft px-4 py-2 text-xs text-danger">
            {error}
          </p>
        )}
        <Composer
          placeholder={`Message ${assistantName}…`}
          disabled={sending}
          onSend={send}
        />
      </section>

      <aside className="flex min-h-[320px] flex-col overflow-hidden rounded-xl border border-border bg-surface lg:h-[calc(100vh-8.5rem)]">
        <header className="border-b border-border px-4 py-3">
          <h2 className="font-semibold">Under the hood</h2>
          <p className="text-xs text-muted">
            Tool calls, guardrail checks and cost for each turn
          </p>
        </header>
        <div className="flex-1 overflow-y-auto">
          <EventTimeline turns={detail?.turns ?? []} events={detail?.events ?? []} />
        </div>
      </aside>
    </div>
  );
}
