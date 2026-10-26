export const ENGINE_URL = process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://localhost:8000";

export type ConversationStatus = "active" | "handed_off";

export interface Business {
  id: string;
  name: string;
  assistant_name: string;
  timezone: string;
  phone: string;
  address: string;
}

export interface Conversation {
  id: string;
  channel: string;
  status: ConversationStatus;
  customer_phone: string | null;
  customer_name: string | null;
  handoff_reason: string | null;
  handoff_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: number;
  role: "customer" | "assistant" | "staff";
  text: string;
  author: string | null;
  turn_id: string | null;
  created_at: string;
}

export interface CheckResult {
  name: string;
  passed: boolean;
  detail: string;
}

export interface ToolEvent {
  id: string;
  call_id: string;
  tool: string;
  side_effect: "read" | "write" | "control";
  arguments: Record<string, unknown>;
  status: "ok" | "error" | "blocked" | "invalid";
  result: unknown;
  summary: string | null;
  checks: CheckResult[];
  duration_ms: number;
  turn_id: string | null;
  created_at: string;
}

export interface Turn {
  id: string;
  model: string | null;
  steps: number;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  outcome: "replied" | "handed_off" | "failed";
  created_at: string;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: Message[];
  events: ToolEvent[];
  turns: Turn[];
}

export class EngineError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${ENGINE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new EngineError(`Can't reach the engine at ${ENGINE_URL}. Is it running?`);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new EngineError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  business: () => request<Business>("/v1/business"),
  startConversation: (customerPhone: string | null) =>
    post<Conversation>("/v1/conversations", { channel: "web", customer_phone: customerPhone }),
  conversation: (id: string) => request<ConversationDetail>(`/v1/conversations/${id}`),
  conversations: (status?: ConversationStatus) =>
    request<Conversation[]>(`/v1/conversations${status ? `?status=${status}` : ""}`),
  sendMessage: (id: string, text: string) => post(`/v1/conversations/${id}/messages`, { text }),
  sendStaffMessage: (id: string, text: string, author: string) =>
    post<Message>(`/v1/conversations/${id}/staff-messages`, { text, author }),
  takeOver: (id: string) => post<Conversation>(`/v1/conversations/${id}/takeover`),
  resume: (id: string) => post<Conversation>(`/v1/conversations/${id}/resume`),
};
