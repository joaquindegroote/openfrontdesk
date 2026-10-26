"use client";

import { useEffect, useRef } from "react";
import type { Message } from "@/lib/api";
import { clockTime } from "@/lib/format";

interface Props {
  messages: Message[];
  assistantName: string;
  // Whose side of the chat this is: the customer's (demo) or the staff's (inbox).
  viewer: "customer" | "staff";
  pending?: string | null;
  empty?: React.ReactNode;
}

export function MessageList({ messages, assistantName, viewer, pending, empty }: Props) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pending]);

  if (messages.length === 0 && !pending) {
    return <div className="grid flex-1 place-items-center p-8 text-center text-sm text-muted">{empty}</div>;
  }

  return (
    <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-4">
      {messages.map((message) => {
        const mine = viewer === "customer" ? message.role === "customer" : message.role !== "customer";
        const label =
          message.role === "customer"
            ? "Customer"
            : message.role === "staff"
              ? `${message.author ?? "Staff"} · front desk`
              : `${assistantName} · AI`;
        return (
          <div key={message.id} className={`flex flex-col gap-1 ${mine ? "items-end" : "items-start"}`}>
            <span className="px-1 text-[11px] text-muted">
              {label} · {clockTime(message.created_at)}
            </span>
            <div
              className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
                message.role === "customer"
                  ? "bg-accent text-white"
                  : message.role === "staff"
                    ? "border border-warn/40 bg-warn-soft"
                    : "border border-border bg-surface-muted"
              }`}
            >
              {message.text}
            </div>
          </div>
        );
      })}
      {pending && (
        <div className="flex items-center gap-2 px-1 text-xs text-muted">
          <span className="flex gap-1">
            <span className="size-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.3s]" />
            <span className="size-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.15s]" />
            <span className="size-1.5 animate-bounce rounded-full bg-muted" />
          </span>
          {pending}
        </div>
      )}
      <div ref={bottom} />
    </div>
  );
}
