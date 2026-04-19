import React from "react";
import { trackSuggestionClicked } from "../lib/datadog-rum";

const SUGGESTIONS = [
  "Pull all structurally deficient bridges in Texas with ADT over 10,000 and last inspection before 2022.",
  "Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?",
  "What water supply projects are recommended for the Corpus Christi region in the TWDB 2026 State Water Plan?",
  "Compare grid resilience investment patterns across southeastern states since 2018 using EIA data.",
];

interface Props {
  onSelect: (text: string) => void;
  disabled?: boolean;
}

export function QuerySuggestions({ onSelect, disabled }: Props) {
  function handleClick(suggestion: string) {
    trackSuggestionClicked(suggestion);
    onSelect(suggestion);
  }

  return (
    <div className="flex flex-wrap gap-2 px-1">
      {SUGGESTIONS.map((s) => (
        <button
          key={s}
          onClick={() => handleClick(s)}
          disabled={disabled}
          className="text-xs bg-gray-100 hover:bg-gray-200 disabled:opacity-50 disabled:cursor-not-allowed text-gray-700 px-3 py-1.5 rounded-full transition-colors text-left"
        >
          {s.length > 60 ? s.slice(0, 60) + "…" : s}
        </button>
      ))}
    </div>
  );
}
