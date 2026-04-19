import React, { useState } from "react";
import { Citation } from "../lib/api";
import { trackCitationExpanded } from "../lib/datadog-rum";

interface Props {
  citations: Citation[];
}

const DOCTYPE_COLORS: Record<string, string> = {
  proposal: "bg-blue-100 text-blue-800",
  close_out_report: "bg-purple-100 text-purple-800",
  water_plan_project: "bg-teal-100 text-teal-800",
  risk_assessment: "bg-orange-100 text-orange-800",
  cost_benchmark: "bg-green-100 text-green-800",
  technical_memo: "bg-gray-100 text-gray-800",
};

function DocTypeBadge({ type }: { type: string }) {
  const colors = DOCTYPE_COLORS[type] ?? "bg-gray-100 text-gray-700";
  const label = type.replace(/_/g, " ");
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors}`}>{label}</span>
  );
}

function CitationItem({ citation }: { citation: Citation }) {
  const [expanded, setExpanded] = useState(false);

  function handleToggle() {
    if (!expanded) {
      trackCitationExpanded(citation.document_type, citation.score);
    }
    setExpanded((v) => !v);
  }

  return (
    <div className="border border-gray-100 rounded-md overflow-hidden">
      <button
        onClick={handleToggle}
        className="w-full text-left px-3 py-2 bg-gray-50 hover:bg-gray-100 transition-colors flex items-center justify-between gap-2"
      >
        <div className="flex items-center gap-2 min-w-0">
          <DocTypeBadge type={citation.document_type} />
          <span className="text-xs text-gray-600 truncate">{citation.content.slice(0, 60)}…</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {citation.score != null && (
            <span className="text-xs text-gray-400">{(citation.score * 100).toFixed(0)}%</span>
          )}
          <svg
            className={`w-3 h-3 text-gray-400 transition-transform ${expanded ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>

      {expanded && (
        <div className="px-3 py-2 bg-white text-xs text-gray-700 leading-relaxed space-y-1">
          <p>{citation.content}</p>
          {citation.source_url && (
            <a
              href={citation.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline"
            >
              Source document →
            </a>
          )}
        </div>
      )}
    </div>
  );
}

export function CitationPanel({ citations }: Props) {
  if (citations.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-xs text-gray-400 text-center px-4">
          Sources will appear here when the agent retrieves knowledge base chunks.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-1.5 overflow-y-auto">
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
        Sources ({citations.length})
      </p>
      {citations.map((c, i) => (
        <CitationItem key={i} citation={c} />
      ))}
    </div>
  );
}
