import React from "react";
import { Button, Flex } from "@chakra-ui/react";
import { trackSuggestionClicked } from "../lib/datadog-rum";

export interface Suggestion {
  label: string;   // short chip label (shown in pill)
  query: string;   // full query sent to the agent
}

interface Props {
  suggestions: Suggestion[];
  onSelect: (text: string) => void;
  disabled?: boolean;
}

// Buttons are direct children of Flex so that CSS selectors like
// `.recommendations-pill:nth-child(2)` work for Synthetics browser tests.
export function QuerySuggestions({ suggestions, onSelect, disabled }: Props) {
  function handleClick(s: Suggestion) {
    trackSuggestionClicked(s.query);
    onSelect(s.query);
  }

  return (
    <Flex wrap="wrap" gap={1.5}>
      {suggestions.map((s) => (
        <Button
          key={s.label}
          className="recommendations-pill"
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
      ))}
    </Flex>
  );
}
