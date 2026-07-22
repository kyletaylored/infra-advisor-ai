import { getRumSessionId } from "./datadog-rum";
import { getToken } from "./auth";

const BACKEND_KEY = "infra_advisor_backend";
export type BackendType = "python" | "dotnet";

// All agent-api endpoints now require Authorization: Bearer <jwt>. Use this
// helper instead of writing the header inline so a forgotten call site
// fails closed with a clear 401 rather than leaking an anonymous path.
function authHeader(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function getBackend(): BackendType {
  return (localStorage.getItem(BACKEND_KEY) as BackendType) || "python";
}

export function setBackend(backend: BackendType): void {
  localStorage.setItem(BACKEND_KEY, backend);
}

export function getApiBase(): string {
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

/** Set the session ID to an explicit value (e.g. a conversation ID loaded from URL or DB). */
export function setSessionId(id: string): void {
  _sessionId = id;
  localStorage.setItem(SESSION_STORAGE_KEY, id);
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
  // Prefer the Datadog RUM session ID so gen_ai.conversation.id in LLMObs maps
  // directly to the RUM session — enabling LLMObs ↔ RUM correlation in Datadog.
  // Fall back to the localStorage-persisted UUID when RUM is not initialised.
  const sessionId = getRumSessionId() ?? getSessionId();

  const response = await fetch(`${getApiBase()}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-ID": sessionId,
      ...(conversationId ? { "X-Conversation-ID": conversationId } : {}),
      ...(userId ? { "X-User-ID": userId } : {}),
      ...rumHeaders(),
      ...authHeader(),
    },
    // session_id in the body is the canonical source — headers can be stripped
    // by proxies but the POST body always reaches the backend.
    body: JSON.stringify({ query, session_id: sessionId, ...(model ? { model } : {}) }),
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

// ── Streaming /query/stream ─────────────────────────────────────────────────
// Server-Sent Events emitted by agent-api-dotnet's POST /query/stream. Each
// event surfaces a step in the agent loop so the UI can render live progress.
// The discriminator is `event` (matches the SSE event-name line); each
// variant's payload mirrors Models/StreamEvent.cs on the backend.

export type StreamEvent =
  | { event: "step"; step: string; status: string; detail?: string | null }
  | { event: "tool_call_start"; id: string; name: string; args_json?: string | null }
  | {
      event: "tool_call_end";
      id: string;
      name: string;
      status: "ok" | "error";
      result_summary?: string | null;
      sources: string[];
      duration_ms: number;
    }
  | { event: "text_chunk"; chunk: string }
  | {
      event: "done";
      trace_id: string | null;
      span_id: string | null;
      session_id: string;
      model: string;
      sources: string[];
      tools_called: string[];
      query_domain: string;
    }
  | { event: "error"; message: string; trace_id: string | null; category?: string | null };

// Streams events from POST /query/stream as an AsyncIterable<StreamEvent>.
// Caller consumes with `for await (const ev of sendQueryStream(...))`.
// On HTTP-level error (non-2xx), yields a single { event: "error", ... }
// then completes. On mid-stream error from the backend, the backend itself
// emits an event: error block; we just pass it through.
export async function* sendQueryStream(
  query: string,
  model?: string,
  conversationId?: string,
  userId?: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent, void, void> {
  const sessionId = getRumSessionId() ?? getSessionId();
  const response = await fetch(`${getApiBase()}/query/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
      "X-Session-ID": sessionId,
      ...(conversationId ? { "X-Conversation-ID": conversationId } : {}),
      ...(userId ? { "X-User-ID": userId } : {}),
      ...rumHeaders(),
      ...authHeader(),
    },
    body: JSON.stringify({ query, session_id: sessionId, ...(model ? { model } : {}) }),
    signal,
  });

  if (!response.ok) {
    const { detail, traceId } = await extractErrorDetail(response);
    console.error(`[api] sendQueryStream ${response.status}:`, detail);
    yield { event: "error", message: detail, trace_id: traceId };
    return;
  }
  if (!response.body) {
    yield { event: "error", message: "Empty streaming response body", trace_id: null };
    return;
  }

  // Standard SSE parsing: dispatch events on blank-line boundaries. We
  // intentionally re-implement rather than pulling in eventsource-parser
  // since the protocol is small and we only handle two field names.
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let blockEnd: number;
      while ((blockEnd = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, blockEnd);
        buffer = buffer.slice(blockEnd + 2);
        const parsed = parseSseBlock(block);
        if (parsed) yield parsed;
      }
    }
    // Drain any final partial block (no trailing blank line).
    if (buffer.trim().length > 0) {
      const parsed = parseSseBlock(buffer);
      if (parsed) yield parsed;
    }
  } finally {
    reader.releaseLock();
  }
}

function parseSseBlock(block: string): StreamEvent | null {
  let eventName: string | null = null;
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!eventName || dataLines.length === 0) return null;
  try {
    const payload = JSON.parse(dataLines.join("\n"));
    return { event: eventName, ...payload } as StreamEvent;
  } catch (err) {
    console.warn("[api] failed to parse SSE block:", err, block);
    return null;
  }
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
        ...authHeader(),
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
  await fetch(`${getApiBase()}/session/${_sessionId}`, {
    method: "DELETE",
    headers: { ...authHeader() },
  });
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
    const response = await fetch(`${getApiBase()}/suggestions/initial`, {
      headers: { ...authHeader() },
    });
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
      headers: { "Content-Type": "application/json", ...authHeader() },
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

// Persisted tool-call / pipeline-step reasoning for an assistant message —
// mirrors StoredStep (agent-api-dotnet) / the steps dict shape (agent-api).
export interface StoredStepDto {
  kind: "tool" | "internal";
  id: string;
  name: string;
  status: string;
  args_json?: string | null;
  result_summary?: string | null;
  sources?: string[] | null;
  duration_ms?: number | null;
  detail?: string | null;
}

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  sources: string[];
  steps?: StoredStepDto[];
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
  _userId: string,  // identity now comes from the JWT sub claim server-side
  title: string,
  model?: string,
  backend?: string,
): Promise<ConversationSummary | null> {
  try {
    const res = await fetch(`${getApiBase()}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeader() },
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

export async function listConversations(_userId: string): Promise<ConversationSummary[]> {
  try {
    const res = await fetch(`${getApiBase()}/conversations`, {
      headers: { ...authHeader() },
    });
    if (!res.ok) {
      const { detail } = await extractErrorDetail(res);
      console.error(`[api] listConversations ${res.status}:`, detail);
      return [];
    }
    const data = await res.json();
    // .NET returns a plain array; Python wraps in { conversations: [...] }
    return Array.isArray(data) ? data : (data.conversations ?? []);
  } catch (err) {
    console.error("[api] listConversations failed:", err);
    return [];
  }
}

export async function getConversation(id: string, _userId: string): Promise<ConversationDetail | null> {
  try {
    const res = await fetch(`${getApiBase()}/conversations/${id}`, {
      headers: { ...authHeader() },
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

export async function deleteConversation(id: string, _userId: string): Promise<boolean> {
  try {
    const res = await fetch(`${getApiBase()}/conversations/${id}`, {
      method: "DELETE",
      headers: { ...authHeader() },
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
