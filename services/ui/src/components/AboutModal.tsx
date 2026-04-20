import React, { useState } from "react";
import {
  Badge,
  Box,
  Button,
  Dialog,
  Flex,
  HStack,
  Text,
  VStack,
} from "@chakra-ui/react";

const DATA_SOURCES = [
  { label: "FHWA NBI",       color: "orange", desc: "National Bridge Inventory — structural ratings & ADT" },
  { label: "OpenFEMA",        color: "red",    desc: "Disaster declarations & hazard mitigation grants" },
  { label: "EIA",             color: "yellow", desc: "State electricity generation, capacity & fuel mix" },
  { label: "EPA SDWIS",       color: "blue",   desc: "Public water system compliance & SDWA violations" },
  { label: "TWDB",            color: "cyan",   desc: "Texas 2026 State Water Plan recommended projects" },
  { label: "ERCOT",           color: "purple", desc: "Texas grid energy storage resource 4-sec data" },
  { label: "TxDOT Open Data", color: "green",  desc: "AADT traffic counts & construction project datasets" },
  { label: "Internal KB",     color: "gray",   desc: "Firm case studies, risk frameworks & templates" },
];

const TIPS = [
  { icon: "📍", text: "Specify geography — \"Harris County, TX\" returns tighter results than \"Texas\"." },
  { icon: "📊", text: "Add thresholds — e.g. \"ADT over 10,000\" or \"inspection before 2022\"." },
  { icon: "📝", text: "Ask for documents — \"Draft a scope of work for…\" generates a structured template." },
  { icon: "💡", text: "Use the suggestion pills after a response to explore related data dimensions." },
  { icon: "🔁", text: "Follow up in the same session — the agent remembers context across turns." },
];

export function AboutModal() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        size="xs"
        variant="ghost"
        colorPalette="gray"
        borderRadius="md"
        fontSize="xs"
        fontWeight="normal"
        color="gray.500"
        px={2}
        h="26px"
        onClick={() => setOpen(true)}
      >
        About
      </Button>

      <Dialog.Root open={open} onOpenChange={(e) => setOpen(e.open)} size="lg">
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content borderRadius="2xl" maxH="85vh" display="flex" flexDirection="column">
            <Dialog.Header borderBottomWidth="1px" borderColor="gray.100" px={6} py={4} flexShrink={0}>
              <HStack justify="space-between" align="center">
                <VStack align="start" gap={0}>
                  <Dialog.Title fontSize="md" fontWeight="semibold" color="gray.800">
                    InfraAdvisor AI
                  </Dialog.Title>
                  <Text fontSize="xs" color="gray.400">
                    AI-powered infrastructure consulting platform
                  </Text>
                </VStack>
                <Dialog.CloseTrigger asChild>
                  <Button size="xs" variant="ghost" colorPalette="gray" borderRadius="md" px={2} h="26px">
                    ✕
                  </Button>
                </Dialog.CloseTrigger>
              </HStack>
            </Dialog.Header>

            <Dialog.Body px={6} py={5} overflowY="auto" flex={1}>
              <VStack align="stretch" gap={5}>
                {/* What it does */}
                <VStack align="start" gap={1.5}>
                  <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
                    What it does
                  </Text>
                  <Text fontSize="sm" color="gray.700" lineHeight="tall">
                    InfraAdvisor AI connects infrastructure consultants to live government datasets via a
                    multi-tool AI agent. Ask questions in plain English — the agent selects the right data
                    sources, retrieves records, and synthesises an actionable response grounded in real data.
                  </Text>
                </VStack>

                {/* Data sources */}
                <VStack align="start" gap={2}>
                  <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
                    Live data sources
                  </Text>
                  <VStack align="stretch" gap={1.5} w="full">
                    {DATA_SOURCES.map((src) => (
                      <Flex key={src.label} align="center" gap={2.5}>
                        <Badge
                          colorPalette={src.color}
                          variant="subtle"
                          fontSize="xs"
                          borderRadius="full"
                          px={2}
                          py={0.5}
                          flexShrink={0}
                          minW="110px"
                          textAlign="center"
                        >
                          {src.label}
                        </Badge>
                        <Text fontSize="xs" color="gray.600">{src.desc}</Text>
                      </Flex>
                    ))}
                  </VStack>
                </VStack>

                {/* Tips */}
                <VStack align="start" gap={2}>
                  <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
                    Tips for better results
                  </Text>
                  <VStack align="stretch" gap={1.5}>
                    {TIPS.map((tip) => (
                      <HStack key={tip.icon} align="start" gap={2}>
                        <Text fontSize="sm" flexShrink={0}>{tip.icon}</Text>
                        <Text fontSize="xs" color="gray.600" lineHeight="tall">{tip.text}</Text>
                      </HStack>
                    ))}
                  </VStack>
                </VStack>

                {/* Architecture */}
                <Box bg="gray.50" borderRadius="xl" p={4} borderWidth="1px" borderColor="gray.100">
                  <Text fontSize="xs" fontWeight="semibold" color="gray.500" mb={2} textTransform="uppercase" letterSpacing="wide">
                    How it works
                  </Text>
                  <HStack gap={1.5} flexWrap="wrap">
                    {["UI", "→", "Agent API", "→", "MCP Server", "→", "Live APIs", "→", "Azure AI Search"].map((step, i) => (
                      <Text key={i} fontSize="xs" color={step === "→" ? "gray.300" : "gray.600"} fontFamily={step === "→" ? "mono" : undefined}>
                        {step}
                      </Text>
                    ))}
                  </HStack>
                  <Text fontSize="xs" color="gray.400" mt={1.5}>
                    Responses are grounded in retrieved data — the agent cites sources for every factual claim.
                  </Text>
                </Box>
              </VStack>
            </Dialog.Body>
          </Dialog.Content>
        </Dialog.Positioner>
      </Dialog.Root>
    </>
  );
}
