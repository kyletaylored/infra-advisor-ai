import { getRumSessionId } from "./datadog-rum";

const AGENT_API_BASE = import.meta.env.VITE_AGENT_API_URL || "/api";

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

export async function sendQuery(query: string, model?: string): Promise<QueryResponse> {
  const response = await fetch(`${AGENT_API_BASE}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-ID": getSessionId(),
      ...rumHeaders(),
    },
    body: JSON.stringify({ query, ...(model ? { model } : {}) }),
  });

  if (!response.ok) {
    let detail: string;
    let traceId: string | null = null;
    try {
      const body = await response.json();
      detail = body.detail ?? JSON.stringify(body);
      traceId = body.trace_id ?? null;
    } catch {
      detail = await response.text();
    }
    throw new ApiError(`Agent API error ${response.status}: ${detail}`, response.status, traceId);
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
  const response = await fetch(`${AGENT_API_BASE}/suggestions`, {
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
}

export async function clearSession(): Promise<void> {
  if (!_sessionId) return;
  await fetch(`${AGENT_API_BASE}/session/${_sessionId}`, { method: "DELETE" });
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
    const response = await fetch(`${AGENT_API_BASE}/suggestions/initial`);
    if (!response.ok) return [];
    const data: SuggestionsResponse = await response.json();
    return data.suggestions ?? [];
  } catch {
    return [];
  }
}

export async function fetchModels(): Promise<ModelsResponse> {
  try {
    const response = await fetch(`${AGENT_API_BASE}/models`);
    if (!response.ok) return { models: ["gpt-4.1-mini"], default: "gpt-4.1-mini" };
    return await response.json();
  } catch {
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
    await fetch(`${AGENT_API_BASE}/feedback`, {
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
