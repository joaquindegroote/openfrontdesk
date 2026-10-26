import type { ConversationStatus, ToolEvent } from "@/lib/api";

const EVENT_STYLES: Record<ToolEvent["status"], string> = {
  ok: "bg-ok-soft text-ok",
  blocked: "bg-warn-soft text-warn",
  error: "bg-danger-soft text-danger",
  invalid: "bg-surface-muted text-muted",
};

export function EventStatusBadge({ status }: { status: ToolEvent["status"] }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium uppercase ${EVENT_STYLES[status]}`}>
      {status}
    </span>
  );
}

export function ConversationStatusBadge({ status }: { status: ConversationStatus }) {
  const handedOff = status === "handed_off";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${
        handedOff ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok"
      }`}
    >
      <span className={`size-1.5 rounded-full ${handedOff ? "bg-warn" : "bg-ok"}`} />
      {handedOff ? "With staff" : "AI active"}
    </span>
  );
}
