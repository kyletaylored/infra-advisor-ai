import React from "react";
import { BridgeData } from "../lib/api";

interface Props {
  bridge: BridgeData;
}

type ConditionLevel = "poor" | "fair" | "good" | "unknown";

function conditionLevel(code: string): ConditionLevel {
  const n = parseInt(code, 10);
  if (isNaN(n)) return "unknown";
  if (n <= 4) return "poor";
  if (n <= 6) return "fair";
  return "good";
}

const CONDITION_COLORS: Record<ConditionLevel, string> = {
  poor: "bg-red-100 text-red-800 border-red-200",
  fair: "bg-amber-100 text-amber-800 border-amber-200",
  good: "bg-green-100 text-green-800 border-green-200",
  unknown: "bg-gray-100 text-gray-600 border-gray-200",
};

function ConditionBadge({ code, label }: { code: string; label: string }) {
  const level = conditionLevel(code);
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${CONDITION_COLORS[level]}`}
    >
      {label}: {code || "N/A"}
    </span>
  );
}

export function BridgeCard({ bridge }: Props) {
  const sufficiency = bridge.sufficiency_rating ?? 0;
  const mapsUrl =
    bridge.latitude && bridge.longitude
      ? `https://www.google.com/maps?q=${bridge.latitude},${bridge.longitude}`
      : null;

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white shadow-sm space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-mono text-gray-500">Structure #{bridge.structure_number}</p>
          <p className="text-sm font-semibold text-gray-900">
            {bridge.county}, {bridge.state}
          </p>
        </div>
        {mapsUrl && (
          <a
            href={mapsUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-600 hover:underline whitespace-nowrap"
          >
            View on map →
          </a>
        )}
      </div>

      {/* Sufficiency rating bar */}
      <div>
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>Sufficiency Rating</span>
          <span className="font-medium">{sufficiency.toFixed(1)}</span>
        </div>
        <div className="w-full bg-gray-100 rounded-full h-2">
          <div
            className={`h-2 rounded-full ${
              sufficiency <= 40 ? "bg-red-500" : sufficiency <= 70 ? "bg-amber-400" : "bg-green-500"
            }`}
            style={{ width: `${Math.min(100, sufficiency)}%` }}
          />
        </div>
      </div>

      {/* Condition badges */}
      <div className="flex flex-wrap gap-1.5">
        <ConditionBadge code={bridge.deck_condition} label="Deck" />
        <ConditionBadge code={bridge.superstructure_condition} label="Super" />
        <ConditionBadge code={bridge.substructure_condition} label="Sub" />
      </div>

      {bridge.last_inspection_date && (
        <p className="text-xs text-gray-400">
          Last inspection: {bridge.last_inspection_date}
        </p>
      )}
    </div>
  );
}
