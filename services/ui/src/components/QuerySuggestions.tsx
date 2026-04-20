import React from "react";
import { Button, Wrap, WrapItem } from "@chakra-ui/react";
import { trackSuggestionClicked } from "../lib/datadog-rum";

interface Suggestion {
  label: string;   // short chip label
  query: string;   // full query sent to the agent
}

const SUGGESTIONS: Suggestion[] = [
  {
    label: "🌉 Deficient Texas bridges",
    query: "Pull all structurally deficient bridges in Texas with ADT over 10,000 and last inspection before 2022.",
  },
  {
    label: "💧 SDWA violations",
    query: "Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?",
  },
  {
    label: "🗺️ Corpus Christi water plan",
    query: "What water supply projects are recommended for the Corpus Christi region in the TWDB 2026 State Water Plan?",
  },
  {
    label: "⚡ Southeast grid resilience",
    query: "Compare grid resilience investment patterns across southeastern states since 2018 using EIA data.",
  },
];

interface Props {
  onSelect: (text: string) => void;
  disabled?: boolean;
}

export function QuerySuggestions({ onSelect, disabled }: Props) {
  function handleClick(s: Suggestion) {
    trackSuggestionClicked(s.query);
    onSelect(s.query);
  }

  return (
    <Wrap gap={1.5}>
      {SUGGESTIONS.map((s) => (
        <WrapItem key={s.label}>
          <Button
            size="xs"
            variant="outline"
            colorPalette="gray"
            borderRadius="full"
            fontWeight="normal"
            fontSize="xs"
            disabled={disabled}
            onClick={() => handleClick(s)}
            px={3}
            h="26px"
            borderColor="gray.200"
            color="gray.600"
            _hover={{ bg: "gray.50", borderColor: "gray.300" }}
          >
            {s.label}
          </Button>
        </WrapItem>
      ))}
    </Wrap>
  );
}
