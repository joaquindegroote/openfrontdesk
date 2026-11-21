import type { TokenUsage } from "./api";

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function relativeTime(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" });
}

export function seconds(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/** How much of a turn's input the model read from its prompt cache, or null if unknown. */
export function percentFromCache(usage: Record<string, TokenUsage> | null): number | null {
  const calls = Object.values(usage ?? {});
  const sent = calls.reduce((sum, call) => sum + call.input_tokens, 0);
  const read = calls.reduce((sum, call) => sum + call.cache_read_tokens, 0);
  return sent > 0 ? Math.round((100 * read) / sent) : null;
}

export function humanize(code: string | null): string {
  if (!code) return "";
  const text = code.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
