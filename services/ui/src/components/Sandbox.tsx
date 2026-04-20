import React, { useState } from "react";
import {
  Badge,
  Box,
  Button,
  Flex,
  HStack,
  Spinner,
  Text,
  Textarea,
  VStack,
} from "@chakra-ui/react";

const AGENT_API_BASE = (import.meta.env.VITE_AGENT_API_URL as string | undefined) || "/api";

// ── Endpoint definitions ─────────────────────────────────────────────────────

interface EndpointDef {
  id: string;
  group: "Agent API" | "MCP Tools";
  label: string;
  method: "GET" | "POST";
  path: string;
  description: string;
  example: object | null;
}

const ENDPOINTS: EndpointDef[] = [
  // ── Agent API ──────────────────────────────────────────────────────────────
  {
    id: "health",
    group: "Agent API",
    label: "GET /health",
    method: "GET",
    path: "/health",
    description: "Liveness probe — returns service status and MCP/LLM connectivity.",
    example: null,
  },
  {
    id: "query",
    group: "Agent API",
    label: "POST /query",
    method: "POST",
    path: "/query",
    description: "Run a natural-language query through the full ReAct agent loop. The agent selects tools, retrieves data, and synthesises a response.",
    example: { query: "What are the most structurally deficient bridges in Harris County, Texas with ADT over 10,000?" },
  },
  {
    id: "suggestions",
    group: "Agent API",
    label: "POST /suggestions",
    method: "POST",
    path: "/suggestions",
    description: "Generate 4 LLM-powered follow-up question suggestions based on the previous conversation turn.",
    example: {
      query: "Show Texas bridges with poor deck conditions",
      answer: "I found 47 bridges with poor deck ratings across Texas...",
      sources: ["get_bridge_condition"],
    },
  },
  // ── MCP Tools ─────────────────────────────────────────────────────────────
  {
    id: "get_bridge_condition",
    group: "MCP Tools",
    label: "get_bridge_condition",
    method: "POST",
    path: "/tools/get_bridge_condition",
    description: "Query the FHWA National Bridge Inventory. state_code must be a 2-digit FIPS numeric code (TX=48, CA=06, FL=12, NY=36).",
    example: { state_code: "48", structurally_deficient_only: true, limit: 5 },
  },
  {
    id: "get_disaster_history",
    group: "MCP Tools",
    label: "get_disaster_history",
    method: "POST",
    path: "/tools/get_disaster_history",
    description: "Query OpenFEMA for major disaster declarations and public assistance data.",
    example: { states: ["TX"], incident_types: ["Hurricane", "Flood"], limit: 10 },
  },
  {
    id: "get_energy_infrastructure",
    group: "MCP Tools",
    label: "get_energy_infrastructure",
    method: "POST",
    path: "/tools/get_energy_infrastructure",
    description: "Query EIA for state-level electricity generation, capacity, or fuel mix data.",
    example: { states: ["TX"], data_series: "generation", year_from: 2020 },
  },
  {
    id: "get_water_infrastructure",
    group: "MCP Tools",
    label: "get_water_infrastructure",
    method: "POST",
    path: "/tools/get_water_infrastructure",
    description: "Query EPA SDWIS for water system compliance data or TWDB 2026 State Water Plan projects.",
    example: { query_type: "water_plan_projects", states: ["TX"], limit: 10 },
  },
  {
    id: "get_ercot_energy_storage",
    group: "MCP Tools",
    label: "get_ercot_energy_storage",
    method: "POST",
    path: "/tools/get_ercot_energy_storage",
    description: "Query ERCOT's public data API for Energy Storage Resource (ESR) 4-second charging data. Texas-specific.",
    example: { query_type: "charging_data", size: 10 },
  },
  {
    id: "search_txdot_open_data",
    group: "MCP Tools",
    label: "search_txdot_open_data",
    method: "POST",
    path: "/tools/search_txdot_open_data",
    description: "Search the TxDOT Open Data portal (ArcGIS Hub) for Texas transportation datasets.",
    example: { query_type: "traffic_counts", county: "Travis", limit: 10 },
  },
  {
    id: "search_project_knowledge",
    group: "MCP Tools",
    label: "search_project_knowledge",
    method: "POST",
    path: "/tools/search_project_knowledge",
    description: "Hybrid semantic + keyword search against the firm's internal knowledge base indexed in Azure AI Search.",
    example: { query: "Texas bridge rehabilitation scour risk", top_k: 5 },
  },
  {
    id: "draft_document",
    group: "MCP Tools",
    label: "draft_document",
    method: "POST",
    path: "/tools/draft_document",
    description: "Generate a structured document scaffold: scope_of_work, risk_summary, cost_estimate_scaffold, or funding_positioning_memo.",
    example: {
      document_type: "scope_of_work",
      project_name: "I-35 Bridge Deck Rehabilitation",
      client_name: "TxDOT",
      context: { location: "Austin, TX", structure_count: 3 },
    },
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function methodColor(method: "GET" | "POST") {
  return method === "GET" ? "green" : "blue";
}

// ── Component ────────────────────────────────────────────────────────────────

interface RunResult {
  status: number;
  body: unknown;
  durationMs: number;
  requestBody: string | null;
}

export function Sandbox() {
  const [selectedId, setSelectedId] = useState<string>("health");
  const [paramsText, setParamsText] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [responseTab, setResponseTab] = useState<"response" | "request">("response");

  const endpoint = ENDPOINTS.find((e) => e.id === selectedId) ?? ENDPOINTS[0];

  function handleSelect(ep: EndpointDef) {
    setSelectedId(ep.id);
    setParamsText(ep.example ? formatJson(ep.example) : "");
    setResult(null);
  }

  async function handleRun() {
    setRunning(true);
    setResult(null);

    const isGet = endpoint.method === "GET";
    const url = `${AGENT_API_BASE}${endpoint.path}`;
    let body: string | null = null;
    let parsedParams: unknown = null;

    if (!isGet) {
      try {
        parsedParams = paramsText.trim() ? JSON.parse(paramsText) : {};
      } catch {
        setResult({ status: 0, body: { error: "Invalid JSON in parameters" }, durationMs: 0, requestBody: paramsText });
        setRunning(false);
        return;
      }
      body = JSON.stringify(parsedParams);
    }

    const start = performance.now();
    try {
      const resp = await fetch(url, {
        method: endpoint.method,
        headers: isGet ? {} : { "Content-Type": "application/json" },
        body: isGet ? undefined : body,
      });

      const durationMs = Math.round(performance.now() - start);
      let respBody: unknown;
      try {
        respBody = await resp.json();
      } catch {
        respBody = await resp.text();
      }

      setResult({ status: resp.status, body: respBody, durationMs, requestBody: body });
      setResponseTab("response");
    } catch (err) {
      const durationMs = Math.round(performance.now() - start);
      setResult({
        status: 0,
        body: { error: err instanceof Error ? err.message : "Network error" },
        durationMs,
        requestBody: body,
      });
    } finally {
      setRunning(false);
    }
  }

  const groups = ["Agent API", "MCP Tools"] as const;

  return (
    <Flex flex={1} minH={0} overflow="hidden" bg="gray.50">
      {/* ── Left: selector + params ────────────────────────────────────────── */}
      <Flex
        direction="column"
        w="340px"
        flexShrink={0}
        bg="white"
        borderRightWidth="1px"
        borderColor="gray.200"
        overflowY="auto"
      >
        {/* Endpoint list */}
        <Box borderBottomWidth="1px" borderColor="gray.100">
          {groups.map((group) => (
            <Box key={group}>
              <Text
                fontSize="10px"
                fontWeight="semibold"
                color="gray.400"
                textTransform="uppercase"
                letterSpacing="wider"
                px={4}
                pt={3}
                pb={1}
              >
                {group}
              </Text>
              {ENDPOINTS.filter((e) => e.group === group).map((ep) => (
                <Flex
                  key={ep.id}
                  px={4}
                  py={2}
                  align="center"
                  gap={2}
                  cursor="pointer"
                  bg={selectedId === ep.id ? "blue.50" : "transparent"}
                  borderLeftWidth="2px"
                  borderLeftColor={selectedId === ep.id ? "blue.500" : "transparent"}
                  _hover={{ bg: selectedId === ep.id ? "blue.50" : "gray.50" }}
                  onClick={() => handleSelect(ep)}
                >
                  <Badge
                    colorPalette={methodColor(ep.method)}
                    variant="subtle"
                    fontSize="9px"
                    borderRadius="sm"
                    px={1}
                    py={0}
                    flexShrink={0}
                    w="32px"
                    textAlign="center"
                  >
                    {ep.method}
                  </Badge>
                  <Text
                    fontSize="xs"
                    color={selectedId === ep.id ? "blue.700" : "gray.700"}
                    fontFamily={ep.group === "MCP Tools" ? "mono" : undefined}
                    fontWeight={selectedId === ep.id ? "medium" : "normal"}
                    noOfLines={1}
                  >
                    {ep.label}
                  </Text>
                </Flex>
              ))}
            </Box>
          ))}
        </Box>
      </Flex>

      {/* ── Right: editor + response ───────────────────────────────────────── */}
      <Flex direction="column" flex={1} minW={0} overflow="hidden">
        {/* Endpoint info bar */}
        <Box
          bg="white"
          borderBottomWidth="1px"
          borderColor="gray.200"
          px={5}
          py={3}
          flexShrink={0}
        >
          <HStack gap={2} mb={0.5}>
            <Badge colorPalette={methodColor(endpoint.method)} variant="subtle" fontSize="xs" borderRadius="sm" px={1.5}>
              {endpoint.method}
            </Badge>
            <Text fontSize="sm" fontFamily="mono" color="gray.700">
              {AGENT_API_BASE}{endpoint.path}
            </Text>
          </HStack>
          <Text fontSize="xs" color="gray.400" noOfLines={2}>{endpoint.description}</Text>
        </Box>

        <Flex flex={1} minH={0} direction={{ base: "column", lg: "row" }} overflow="hidden">
          {/* Params editor */}
          <Flex
            direction="column"
            w={{ base: "full", lg: "360px" }}
            flexShrink={0}
            borderRightWidth={{ lg: "1px" }}
            borderColor="gray.200"
            p={4}
            gap={3}
          >
            <Text fontSize="xs" fontWeight="semibold" color="gray.500" textTransform="uppercase" letterSpacing="wide">
              Parameters
            </Text>

            {endpoint.method === "GET" ? (
              <Box
                bg="gray.50"
                borderRadius="lg"
                borderWidth="1px"
                borderColor="gray.200"
                p={3}
                flex={1}
              >
                <Text fontSize="xs" color="gray.400" fontStyle="italic">No request body — GET request</Text>
              </Box>
            ) : (
              <Textarea
                value={paramsText}
                onChange={(e) => setParamsText(e.target.value)}
                placeholder="{}"
                fontFamily="mono"
                fontSize="xs"
                resize="none"
                flex={1}
                minH="200px"
                bg="gray.50"
                borderColor="gray.200"
                borderRadius="lg"
                _focus={{ borderColor: "blue.400", bg: "white", boxShadow: "0 0 0 1px var(--chakra-colors-blue-400)" }}
                spellCheck={false}
              />
            )}

            <HStack gap={2}>
              {endpoint.example && (
                <Button
                  size="xs"
                  variant="outline"
                  colorPalette="gray"
                  borderRadius="md"
                  fontSize="xs"
                  onClick={() => setParamsText(formatJson(endpoint.example))}
                >
                  Reset example
                </Button>
              )}
              <Button
                size="sm"
                colorPalette="blue"
                borderRadius="lg"
                flex={1}
                disabled={running}
                onClick={handleRun}
              >
                {running ? <Spinner size="xs" /> : null}
                {running ? "Running…" : "Run"}
              </Button>
            </HStack>
          </Flex>

          {/* Response panel */}
          <Flex direction="column" flex={1} minW={0} p={4} gap={3} overflowY="auto">
            {/* Response tabs */}
            <HStack gap={0} borderBottomWidth="1px" borderColor="gray.100">
              {(["response", "request"] as const).map((tab) => (
                <Button
                  key={tab}
                  size="xs"
                  variant="ghost"
                  borderRadius="none"
                  borderBottomWidth="2px"
                  borderBottomColor={responseTab === tab ? "blue.500" : "transparent"}
                  color={responseTab === tab ? "blue.600" : "gray.500"}
                  fontWeight={responseTab === tab ? "semibold" : "normal"}
                  px={3}
                  h="32px"
                  onClick={() => setResponseTab(tab)}
                  textTransform="capitalize"
                >
                  {tab}
                </Button>
              ))}
              {result && (
                <HStack gap={2} ml="auto" pb={1}>
                  <Badge
                    colorPalette={result.status >= 200 && result.status < 300 ? "green" : result.status === 0 ? "red" : "orange"}
                    variant="subtle"
                    fontSize="xs"
                    borderRadius="full"
                    px={2}
                  >
                    {result.status === 0 ? "Error" : result.status}
                  </Badge>
                  <Text fontSize="xs" color="gray.400">{result.durationMs}ms</Text>
                </HStack>
              )}
            </HStack>

            {!result && !running && (
              <Flex flex={1} align="center" justify="center">
                <VStack gap={2}>
                  <Text fontSize="sm" color="gray.300">No response yet</Text>
                  <Text fontSize="xs" color="gray.300">Hit Run to execute the request</Text>
                </VStack>
              </Flex>
            )}

            {running && (
              <Flex flex={1} align="center" justify="center">
                <HStack gap={2} color="gray.400">
                  <Spinner size="sm" />
                  <Text fontSize="sm">Waiting for response…</Text>
                </HStack>
              </Flex>
            )}

            {result && !running && (
              <Box
                fontFamily="mono"
                fontSize="xs"
                color="gray.700"
                bg="gray.50"
                borderRadius="xl"
                borderWidth="1px"
                borderColor="gray.200"
                p={4}
                overflowX="auto"
                whiteSpace="pre"
                flex={1}
                minH="200px"
              >
                {responseTab === "response"
                  ? formatJson(result.body)
                  : [
                      `${endpoint.method} ${AGENT_API_BASE}${endpoint.path}`,
                      "Content-Type: application/json",
                      "",
                      result.requestBody ? formatJson(JSON.parse(result.requestBody)) : "(no body)",
                    ].join("\n")}
              </Box>
            )}
          </Flex>
        </Flex>
      </Flex>
    </Flex>
  );
}
