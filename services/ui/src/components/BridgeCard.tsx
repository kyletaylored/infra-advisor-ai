import React from "react";
import {
  Badge,
  Box,
  Flex,
  HStack,
  Link,
  Progress,
  Text,
  VStack,
} from "@chakra-ui/react";
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

const CONDITION_SCHEME: Record<ConditionLevel, string> = {
  poor: "red",
  fair: "orange",
  good: "green",
  unknown: "gray",
};

const PROGRESS_COLOR: Record<ConditionLevel, string> = {
  poor: "red",
  fair: "orange",
  good: "green",
  unknown: "gray",
};

function ConditionBadge({ code, label }: { code: string; label: string }) {
  const level = conditionLevel(code);
  return (
    <Badge colorPalette={CONDITION_SCHEME[level]} variant="subtle" fontSize="xs">
      {label}: {code || "N/A"}
    </Badge>
  );
}

export function BridgeCard({ bridge }: Props) {
  const sufficiency = bridge.sufficiency_rating ?? 0;
  const progressLevel: ConditionLevel = sufficiency <= 40 ? "poor" : sufficiency <= 70 ? "fair" : "good";
  const mapsUrl =
    bridge.latitude && bridge.longitude
      ? `https://www.google.com/maps?q=${bridge.latitude},${bridge.longitude}`
      : null;

  return (
    <Box
      borderWidth="1px"
      borderColor="gray.200"
      borderRadius="lg"
      p={4}
      bg="gray.50"
      boxShadow="xs"
    >
      <VStack gap={3} align="stretch">
        {/* Header row */}
        <Flex justify="space-between" align="flex-start" gap={2}>
          <Box>
            <Text fontSize="xs" fontFamily="mono" color="gray.500">
              Structure #{bridge.structure_number}
            </Text>
            <Text fontSize="sm" fontWeight="semibold" color="gray.800">
              {bridge.county}, {bridge.state}
            </Text>
          </Box>
          {mapsUrl && (
            <Link
              href={mapsUrl}
              target="_blank"
              rel="noopener noreferrer"
              fontSize="xs"
              color="blue.600"
              whiteSpace="nowrap"
              _hover={{ textDecoration: "underline" }}
            >
              View map →
            </Link>
          )}
        </Flex>

        {/* Sufficiency rating */}
        <Box>
          <Flex justify="space-between" mb={1}>
            <Text fontSize="xs" color="gray.500">Sufficiency Rating</Text>
            <Text fontSize="xs" fontWeight="medium" color="gray.700">
              {sufficiency.toFixed(1)}
            </Text>
          </Flex>
          <Progress.Root
            value={Math.min(100, sufficiency)}
            colorPalette={PROGRESS_COLOR[progressLevel]}
            borderRadius="full"
            size="sm"
            bg="gray.200"
          >
            <Progress.Track>
              <Progress.Range />
            </Progress.Track>
          </Progress.Root>
        </Box>

        {/* Condition badges */}
        <HStack flexWrap="wrap" gap={1.5}>
          <ConditionBadge code={bridge.deck_condition} label="Deck" />
          <ConditionBadge code={bridge.superstructure_condition} label="Super" />
          <ConditionBadge code={bridge.substructure_condition} label="Sub" />
        </HStack>

        {bridge.last_inspection_date && (
          <Text fontSize="xs" color="gray.400">
            Last inspection: {bridge.last_inspection_date}
          </Text>
        )}
      </VStack>
    </Box>
  );
}
