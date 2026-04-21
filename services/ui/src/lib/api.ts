const AGENT_API_BASE = import.meta.env.VITE_AGENT_API_URL || "/api";

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

let _sessionId: string | null = null;

function getSessionId(): string {
  if (!_sessionId) {
    _sessionId = crypto.randomUUID();
  }
  return _sessionId;
}

export async function sendQuery(query: string, model?: string): Promise<QueryResponse> {
  const response = await fetch(`${AGENT_API_BASE}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-ID": getSessionId(),
    },
    body: JSON.stringify({ query, ...(model ? { model } : {}) }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Agent API error ${response.status}: ${text}`);
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
