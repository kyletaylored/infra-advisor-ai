import React from "react";
import { Button, Wrap, WrapItem } from "@chakra-ui/react";
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
    <Wrap gap={1.5}>
      {SUGGESTIONS.map((s) => (
        <WrapItem key={s}>
          <Button
            size="xs"
            variant="outline"
            colorPalette="gray"
            borderRadius="full"
            fontWeight="normal"
            disabled={disabled}
            onClick={() => handleClick(s)}
            maxW="56"
            whiteSpace="normal"
            h="auto"
            py={1.5}
            textAlign="left"
            justifyContent="flex-start"
          >
            {s.length > 60 ? s.slice(0, 60) + "…" : s}
          </Button>
        </WrapItem>
      ))}
    </Wrap>
  );
}
