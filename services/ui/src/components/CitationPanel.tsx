import React from "react";
import { Badge, Box, Flex, Separator, Text, VStack } from "@chakra-ui/react";
import { Citation } from "../lib/api";
import { trackCitationExpanded } from "../lib/datadog-rum";

interface Props {
  citations: Citation[];
}

const DOCTYPE_PALETTE: Record<string, string> = {
  Bridge:   "blue",
  Disaster: "red",
  Energy:   "yellow",
  Water:    "teal",
  Document: "purple",
  Tool:     "gray",
};

const DOCTYPE_ICON: Record<string, string> = {
  Bridge:   "🌉",
  Disaster: "🌊",
  Energy:   "⚡",
  Water:    "💧",
  Document: "📄",
  Tool:     "🔧",
};

function SourceItem({ citation, index }: { citation: Citation; index: number }) {
  const palette = DOCTYPE_PALETTE[citation.document_type] ?? "gray";
  const icon = DOCTYPE_ICON[citation.document_type] ?? "🔧";

  return (
    <Flex
      gap={3}
      align="flex-start"
      py={2.5}
      px={3}
      borderRadius="lg"
      _hover={{ bg: "gray.50" }}
      cursor="default"
      onClick={() => trackCitationExpanded(citation.document_type, citation.score)}
    >
      <Flex
        w={8}
        h={8}
        borderRadius="md"
        bg={`${palette}.50`}
        align="center"
        justify="center"
        flexShrink={0}
        fontSize="sm"
      >
        {icon}
      </Flex>
      <Box flex={1} minW={0}>
        <Flex align="center" justify="space-between" gap={1} mb={0.5}>
          <Badge
            colorPalette={palette}
            variant="subtle"
            fontSize="xs"
            borderRadius="full"
            px={1.5}
          >
            {citation.document_type}
          </Badge>
          {citation.score != null && (
            <Text fontSize="xs" color="gray.400" fontFamily="mono">
              {(citation.score * 100).toFixed(0)}%
            </Text>
          )}
        </Flex>
        <Text fontSize="xs" color="gray.600" lineHeight="snug">
          {citation.content}
        </Text>
      </Box>
    </Flex>
  );
}

export function CitationPanel({ citations }: Props) {
  return (
    <VStack gap={0} align="stretch" h="full">
      {/* Panel header */}
      <Flex align="center" justify="space-between" mb={3}>
        <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase" letterSpacing="wider">
          Sources
        </Text>
        {citations.length > 0 && (
          <Badge variant="solid" colorPalette="blue" fontSize="xs" borderRadius="full" px={1.5} minW={5} textAlign="center">
            {citations.length}
          </Badge>
        )}
      </Flex>

      <Separator mb={3} />

      {citations.length === 0 ? (
        <Flex flex={1} align="center" justify="center" direction="column" gap={2} py={8}>
          <Text fontSize="xl">🔍</Text>
          <Text fontSize="xs" color="gray.400" textAlign="center" lineHeight="snug">
            Tools used by the agent will appear here after a query.
          </Text>
        </Flex>
      ) : (
        <VStack gap={0} align="stretch">
          {citations.map((citation, i) => (
            <SourceItem key={i} citation={citation} index={i} />
          ))}
        </VStack>
      )}
    </VStack>
  );
}
