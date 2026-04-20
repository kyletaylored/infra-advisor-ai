const AGENT_API_BASE = import.meta.env.VITE_AGENT_API_URL || "/api";

export interface QueryResponse {
  answer: string;
  sources: string[];
  trace_id: string | null;
  session_id: string;
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

export async function sendQuery(query: string): Promise<QueryResponse> {
  const response = await fetch(`${AGENT_API_BASE}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-ID": getSessionId(),
    },
    body: JSON.stringify({ query }),
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
