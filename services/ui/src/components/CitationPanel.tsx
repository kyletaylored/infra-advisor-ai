import React from "react";
import {
  Accordion,
  Badge,
  Box,
  Link,
  Text,
  VStack,
} from "@chakra-ui/react";
import { Citation } from "../lib/api";
import { trackCitationExpanded } from "../lib/datadog-rum";

interface Props {
  citations: Citation[];
}

const DOCTYPE_SCHEME: Record<string, string> = {
  proposal: "blue",
  close_out_report: "purple",
  water_plan_project: "teal",
  risk_assessment: "orange",
  cost_benchmark: "green",
  technical_memo: "gray",
};

function DocTypeBadge({ type }: { type: string }) {
  const scheme = DOCTYPE_SCHEME[type] ?? "gray";
  return (
    <Badge colorPalette={scheme} variant="subtle" fontSize="xs" flexShrink={0}>
      {type.replace(/_/g, " ")}
    </Badge>
  );
}

export function CitationPanel({ citations }: Props) {
  if (citations.length === 0) {
    return (
      <Box h="full" display="flex" alignItems="center" justifyContent="center">
        <Text fontSize="xs" color="gray.400" textAlign="center" px={4}>
          Sources will appear here when the agent retrieves knowledge base chunks.
        </Text>
      </Box>
    );
  }

  return (
    <VStack gap={2} align="stretch">
      <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
        Sources ({citations.length})
      </Text>

      <Accordion.Root multiple>
        {citations.map((citation, i) => (
          <Accordion.Item
            key={i}
            value={String(i)}
            border="1px solid"
            borderColor="gray.100"
            borderRadius="md"
            mb={1.5}
            overflow="hidden"
          >
            <Accordion.ItemTrigger
              px={3}
              py={2}
              bg="gray.50"
              _hover={{ bg: "gray.100" }}
              onClick={() => {
                trackCitationExpanded(citation.document_type, citation.score);
              }}
            >
              <Box flex={1} textAlign="left" minW={0}>
                <DocTypeBadge type={citation.document_type} />
                <Text fontSize="xs" color="gray.600" lineClamp={1} mt={1}>
                  {citation.content.slice(0, 70)}…
                </Text>
              </Box>
              <Box display="flex" alignItems="center" gap={2} ml={2} flexShrink={0}>
                {citation.score != null && (
                  <Text fontSize="xs" color="gray.400">
                    {(citation.score * 100).toFixed(0)}%
                  </Text>
                )}
                <Accordion.ItemIndicator color="gray.400" boxSize={3} />
              </Box>
            </Accordion.ItemTrigger>

            <Accordion.ItemContent px={3} py={2} bg="white">
              <Text fontSize="xs" color="gray.700" lineHeight="tall">
                {citation.content}
              </Text>
              {citation.source_url && (
                <Link
                  href={citation.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  fontSize="xs"
                  color="blue.600"
                  mt={1}
                  display="block"
                  _hover={{ textDecoration: "underline" }}
                >
                  Source document →
                </Link>
              )}
            </Accordion.ItemContent>
          </Accordion.Item>
        ))}
      </Accordion.Root>
    </VStack>
  );
}
