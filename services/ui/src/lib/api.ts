import { getRumSessionId } from "./datadog-rum";

const BACKEND_KEY = "infra_advisor_backend";
export type BackendType = "python" | "dotnet";

export function getBackend(): BackendType {
  return (localStorage.getItem(BACKEND_KEY) as BackendType) || "python";
}

export function setBackend(backend: BackendType): void {
  localStorage.setItem(BACKEND_KEY, backend);
}

function getApiBase(): string {
  return getBackend() === "dotnet" ? "/api-dotnet" : (import.meta.env.VITE_AGENT_API_URL || "/api");
}

export class ApiError extends Error {
  status: number;
  traceId: string | null;
  constructor(message: string, status: number, traceId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.traceId = traceId;
  }
}

export interface QueryResponse {
  answer: string;
  sources: string[];
  trace_id: string | null;
  span_id: string | null;
  session_id: string;
  model: string;
}

export interface Citation {
  content: string;
  document_type: string;
  score?: number;
  source_url?: string;
  tool_name?: string;
  data_notes?: string;
}

export interface BridgeData {
  structure_number: string;
  county: string;
  state: string;
  sufficiency_rating: number;
  deck_condition: string;
  superstructure_condition: string;
  substructure_condition: string;
  last_inspection_date?: string;
  latitude?: number;
  longitude?: number;
  _source: string;
}

const MODEL_STORAGE_KEY = "infra_advisor_model";

export function getModel(): string {
  return localStorage.getItem(MODEL_STORAGE_KEY) || "gpt-4.1-mini";
}

export function setModel(model: string): void {
  localStorage.setItem(MODEL_STORAGE_KEY, model);
}

const SESSION_STORAGE_KEY = "infra_advisor_session_id";

let _sessionId: string | null = null;

function getSessionId(): string {
  if (!_sessionId) {
    _sessionId = localStorage.getItem(SESSION_STORAGE_KEY) ?? crypto.randomUUID();
    localStorage.setItem(SESSION_STORAGE_KEY, _sessionId);
  }
  return _sessionId;
}

/** Start a new conversation — clears memory, generates a fresh session ID. */
export function newConversation(): string {
  _sessionId = crypto.randomUUID();
  localStorage.setItem(SESSION_STORAGE_KEY, _sessionId);
  return _sessionId;
}

function rumHeaders(): Record<string, string> {
  const rumSessionId = getRumSessionId();
  return rumSessionId ? { "X-DD-RUM-Session-ID": rumSessionId } : {};
}

/** Read a Response body once and return a user-friendly error string + optional trace ID. */
async function extractErrorDetail(
  response: Response,
): Promise<{ detail: string; traceId: string | null }> {
  // Read body as text first — body stream can only be consumed once.
  // Then try to parse as JSON; fall back to raw text or a generic message.
  let raw = "";
  try {
    raw = await response.text();
  } catch (e) {
    console.error("[api] Failed to read error response body:", e);
  }
  try {
    const body = JSON.parse(raw);
    return {
      detail: body.detail ?? body.message ?? JSON.stringify(body),
      traceId: body.trace_id ?? null,
    };
  } catch {
    // Non-JSON body (nginx 502 HTML, Cloudflare error page, etc.)
    const isHtml = raw.trimStart().startsWith("<");
    return {
      detail: isHtml
        ? `Backend unavailable (HTTP ${response.status})`
        : raw.trim() || `HTTP ${response.status}`,
      traceId: null,
    };
  }
}

export async function sendQuery(
  query: string,
  model?: string,
  conversationId?: string,
  userId?: string,
): Promise<QueryResponse> {
  const response = await fetch(`${getApiBase()}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-ID": getSessionId(),
      ...(conversationId ? { "X-Conversation-ID": conversationId } : {}),
      ...(userId ? { "X-User-ID": userId } : {}),
      ...rumHeaders(),
    },
    body: JSON.stringify({ query, ...(model ? { model } : {}) }),
  });

  if (!response.ok) {
    const { detail, traceId } = await extractErrorDetail(response);
    console.error(`[api] sendQuery ${response.status}:`, detail);
    throw new ApiError(detail, response.status, traceId);
  }

  const data: QueryResponse = await response.json();

  // Update session ID from server response (server may assign one if missing)
  if (data.session_id) {
    _sessionId = data.session_id;
  }

  return data;
}

export interface SuggestionItem {
  label: string;
  query: string;
}

export interface SuggestionsResponse {
  suggestions: SuggestionItem[];
}

export async function fetchSuggestions(
  query: string,
  answer: string,
  sources: string[],
): Promise<SuggestionItem[]> {
  try {
    const response = await fetch(`${getApiBase()}/suggestions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Session-ID": getSessionId(),
        ...rumHeaders(),
      },
      body: JSON.stringify({ query, answer, sources }),
    });
    if (!response.ok) return [];
    const data: SuggestionsResponse = await response.json();
    return data.suggestions ?? [];
  } catch (err) {
    console.error("[api] fetchSuggestions failed:", err);
    return [];
  }
}

export async function clearSession(): Promise<void> {
  if (!_sessionId) return;
  await fetch(`${getApiBase()}/session/${_sessionId}`, { method: "DELETE" });
  _sessionId = null;
  localStorage.removeItem(SESSION_STORAGE_KEY);
}

/**
 * Attempt to extract bridge data objects from agent answer text or sources.
 * In production this would parse structured JSON returned by the agent;
 * this implementation provides a type-safe stub.
 */
export function extractBridgeData(_answer: string): BridgeData[] {
  return [];
}

export interface ModelsResponse {
  models: string[];
  default: string;
}

export async function fetchInitialSuggestions(): Promise<SuggestionItem[]> {
  try {
    const response = await fetch(`${getApiBase()}/suggestions/initial`);
    if (!response.ok) return [];
    const data: SuggestionsResponse = await response.json();
    return data.suggestions ?? [];
  } catch (err) {
    console.error("[api] fetchInitialSuggestions failed:", err);
    return [];
  }
}

export async function fetchModels(): Promise<ModelsResponse> {
  try {
    const response = await fetch(`${getApiBase()}/models`);
    if (!response.ok) return { models: ["gpt-4.1-mini"], default: "gpt-4.1-mini" };
    return await response.json();
  } catch (err) {
    console.error("[api] fetchModels failed:", err);
    return { models: ["gpt-4.1-mini"], default: "gpt-4.1-mini" };
  }
}

export type FeedbackRating = "positive" | "negative" | "reported";

export async function submitFeedback(
  traceId: string,
  spanId: string,
  rating: FeedbackRating,
): Promise<void> {
  try {
    await fetch(`${getApiBase()}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trace_id: traceId,
        span_id: spanId,
        rating,
        session_id: getSessionId(),
      }),
    });
  } catch {
    // fire-and-forget — feedback failures are non-fatal
  }
}

// ── Conversation history ──────────────────────────────────────────────────────

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  sources: string[];
  trace_id: string | null;
  span_id: string | null;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  user_id: string;
  title: string;
  model: string | null;
  backend: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export async function createConversation(
  userId: string,
  title: string,
  model?: string,
  backend?: string,
): Promise<ConversationSummary | null> {
  try {
    const res = await fetch(`${getApiBase()}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-ID": userId },
      body: JSON.stringify({ title, model, backend }),
    });
    if (!res.ok) {
      const { detail } = await extractErrorDetail(res);
      console.error(`[api] createConversation ${res.status}:`, detail);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.error("[api] createConversation failed:", err);
    return null;
  }
}

export async function listConversations(userId: string): Promise<ConversationSummary[]> {
  try {
    const res = await fetch(`${getApiBase()}/conversations`, {
      headers: { "X-User-ID": userId },
    });
    if (!res.ok) {
      const { detail } = await extractErrorDetail(res);
      console.error(`[api] listConversations ${res.status}:`, detail);
      return [];
    }
    const data = await res.json();
    return data.conversations ?? [];
  } catch (err) {
    console.error("[api] listConversations failed:", err);
    return [];
  }
}

export async function getConversation(id: string, userId: string): Promise<ConversationDetail | null> {
  try {
    const res = await fetch(`${getApiBase()}/conversations/${id}`, {
      headers: { "X-User-ID": userId },
    });
    if (!res.ok) {
      const { detail } = await extractErrorDetail(res);
      console.error(`[api] getConversation ${res.status}:`, detail);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.error("[api] getConversation failed:", err);
    return null;
  }
}

export async function deleteConversation(id: string, userId: string): Promise<boolean> {
  try {
    const res = await fetch(`${getApiBase()}/conversations/${id}`, {
      method: "DELETE",
      headers: { "X-User-ID": userId },
    });
    if (!res.ok) {
      const { detail } = await extractErrorDetail(res);
      console.error(`[api] deleteConversation ${res.status}:`, detail);
    }
    return res.ok;
  } catch (err) {
    console.error("[api] deleteConversation failed:", err);
    return false;
  }
}
