import React, { useState } from "react";
import { Badge, Box, Flex, Link, Separator, Text, VStack } from "@chakra-ui/react";
import { ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import { Citation } from "../lib/api";
import { trackCitationExpanded } from "../lib/datadog-rum";

interface Props {
  citations: Citation[];
}

const DOCTYPE_PALETTE: Record<string, string> = {
  Bridge:         "blue",
  Disaster:       "red",
  Energy:         "yellow",
  Water:          "teal",
  Transportation: "cyan",
  Document:       "purple",
  Procurement:    "green",
  Tool:           "gray",
};

const DOCTYPE_ICON: Record<string, string> = {
  Bridge:         "🌉",
  Disaster:       "🌊",
  Energy:         "⚡",
  Water:          "💧",
  Transportation: "🛣️",
  Document:       "📄",
  Procurement:    "📋",
  Tool:           "🔧",
};

function SourceItem({ citation, index }: { citation: Citation; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const palette = DOCTYPE_PALETTE[citation.document_type] ?? "gray";
  const icon = DOCTYPE_ICON[citation.document_type] ?? "🔧";
  const hasDetail = !!(citation.data_notes || citation.source_url);

  function handleToggle() {
    if (!expanded) trackCitationExpanded(citation.document_type, citation.score);
    setExpanded((v) => !v);
  }

  return (
    <Box borderRadius="lg" overflow="hidden" _hover={{ bg: "gray.50" }} transition="background 0.1s">
      <Flex
        gap={3}
        align="flex-start"
        py={2.5}
        px={3}
        cursor={hasDetail ? "pointer" : "default"}
        onClick={hasDetail ? handleToggle : undefined}
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
            <Flex align="center" gap={1}>
              {citation.score != null && (
                <Text fontSize="xs" color="gray.400" fontFamily="mono">
                  {(citation.score * 100).toFixed(0)}%
                </Text>
              )}
              {hasDetail && (
                <Box color="gray.400" w={3} h={3} flexShrink={0}>
                  {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                </Box>
              )}
            </Flex>
          </Flex>
          <Text fontSize="xs" color="gray.600" lineHeight="snug">
            {citation.content}
          </Text>
        </Box>
      </Flex>

      {expanded && hasDetail && (
        <Box px={3} pb={3} pt={0}>
          <Separator mb={2} />
          {citation.data_notes && (
            <Text fontSize="xs" color="gray.500" lineHeight="relaxed" mb={2}>
              {citation.data_notes}
            </Text>
          )}
          {citation.source_url && (
            <Link
              href={citation.source_url}
              target="_blank"
              rel="noopener noreferrer"
              display="inline-flex"
              alignItems="center"
              gap={1}
              fontSize="xs"
              color="blue.500"
              _hover={{ color: "blue.600", textDecoration: "underline" }}
              fontWeight="medium"
            >
              View data source
              <ExternalLink size={10} />
            </Link>
          )}
        </Box>
      )}
    </Box>
  );
}

export function CitationPanel({ citations }: Props) {
  return (
    <VStack gap={0} align="stretch" h="full">
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
