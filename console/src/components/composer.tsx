"use client";

import { useState } from "react";

interface Props {
  placeholder: string;
  disabled?: boolean;
  onSend: (text: string) => void | Promise<void>;
}

export function Composer({ placeholder, disabled, onSend }: Props) {
  const [text, setText] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabled) return;
    setText("");
    await onSend(value);
  }

  return (
    <form onSubmit={submit} className="flex gap-2 border-t border-border p-3">
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="min-w-0 flex-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm outline-none focus:border-accent disabled:opacity-60"
      />
      <button
        type="submit"
        disabled={disabled || !text.trim()}
        className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-40"
      >
        Send
      </button>
    </form>
  );
}
