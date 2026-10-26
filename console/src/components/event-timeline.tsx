import type { ToolEvent, Turn } from "@/lib/api";
import { seconds } from "@/lib/format";
import { EventStatusBadge } from "./badges";

export function EventTimeline({ turns, events }: { turns: Turn[]; events: ToolEvent[] }) {
  if (turns.length === 0) {
    return (
      <p className="p-4 text-sm text-muted">
        Every tool call, guardrail check and turn metric shows up here as the conversation runs.
      </p>
    );
  }
  return (
    <ol className="flex flex-col gap-4 p-4">
      {turns.map((turn, index) => {
        const turnEvents = events.filter((event) => event.turn_id === turn.id);
        return (
          <li key={turn.id} className="flex flex-col gap-2">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs text-muted">
              <span className="font-medium text-foreground">Turn {index + 1}</span>
              <span>
                {turn.steps} {turn.steps === 1 ? "step" : "steps"} · {seconds(turn.latency_ms)} ·{" "}
                {(turn.input_tokens + turn.output_tokens).toLocaleString()} tokens
                {turn.outcome !== "replied" && (
                  <span className="ml-1 font-medium text-warn">· {turn.outcome.replace("_", " ")}</span>
                )}
              </span>
            </div>
            {turnEvents.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-3 py-2 text-xs text-muted">
                Answered without tools.
              </p>
            ) : (
              turnEvents.map((event) => <EventCard key={event.id} event={event} />)
            )}
          </li>
        );
      })}
    </ol>
  );
}

function EventCard({ event }: { event: ToolEvent }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-3 text-xs">
      <div className="flex items-center gap-2">
        <code className="font-mono text-[12px] font-medium">{event.tool}</code>
        {event.side_effect !== "read" && (
          <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10px] font-medium uppercase text-accent">
            {event.side_effect}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2 text-muted">
          {seconds(event.duration_ms)}
          <EventStatusBadge status={event.status} />
        </span>
      </div>
      {event.summary && <p className="mt-2 text-muted">{event.summary}</p>}
      {event.checks.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {event.checks.map((check) => (
            <li key={check.name} className="flex gap-2">
              <span className={check.passed ? "text-ok" : "text-warn"}>{check.passed ? "✓" : "✗"}</span>
              <span>
                <span className="font-mono">{check.name}</span>
                <span className="text-muted"> · {check.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <details className="mt-2">
        <summary className="cursor-pointer select-none text-muted hover:text-foreground">
          Arguments and result
        </summary>
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-surface-muted p-2 font-mono text-[11px] leading-relaxed">
          {JSON.stringify({ arguments: event.arguments, result: event.result }, null, 2)}
        </pre>
      </details>
    </div>
  );
}
