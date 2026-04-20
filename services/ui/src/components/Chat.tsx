import React, { useEffect, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Circle,
  Flex,
  Grid,
  HStack,
  IconButton,
  Link,
  Separator,
  Spinner,
  Text,
  Textarea,
  VStack,
} from "@chakra-ui/react";
import { BridgeData, Citation, QueryResponse, extractBridgeData, sendQuery } from "../lib/api";
import { trackBridgeCardRendered, trackQuerySubmitted } from "../lib/datadog-rum";
import { BridgeCard } from "./BridgeCard";
import { CitationPanel } from "./CitationPanel";
import { QuerySuggestions } from "./QuerySuggestions";

const VERSION = import.meta.env.VITE_APP_VERSION as string | undefined;
const REPO_URL = "https://github.com/kyletaylored/infra-advisor-ai";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources: string[];
  citations: Citation[];
  bridges: BridgeData[];
  traceId?: string | null;
}

const TOOL_META: Record<string, { label: string; document_type: string; description: string }> = {
  get_bridge_condition:      { label: "Bridge Condition",      document_type: "Bridge",   description: "FHWA National Bridge Inventory" },
  get_disaster_history:      { label: "Disaster History",      document_type: "Disaster", description: "OpenFEMA disaster declarations" },
  get_energy_infrastructure: { label: "Energy Infrastructure", document_type: "Energy",   description: "EIA energy infrastructure data" },
  get_water_infrastructure:  { label: "Water Infrastructure",  document_type: "Water",    description: "Texas Water Development Board plans" },
  search_project_knowledge:  { label: "Knowledge Base",        document_type: "Document", description: "Azure AI Search hybrid index" },
  draft_document:            { label: "Draft Document",        document_type: "Document", description: "Jinja2 consulting document template" },
};

function sourceToCitation(tool: string): Citation {
  const meta = TOOL_META[tool];
  return {
    content: meta ? meta.description : tool,
    document_type: meta ? meta.document_type : "Tool",
  };
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none"
      stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14m-7-7l7 7-7 7" />
    </svg>
  );
}

// ── Domain tiles shown in the empty state ────────────────────────────────────

const DOMAINS = [
  { icon: "🌉", label: "Bridges",  sub: "NBI structural conditions" },
  { icon: "🌊", label: "Disasters", sub: "FEMA declarations & risk" },
  { icon: "⚡", label: "Energy",    sub: "EIA grid & generation" },
  { icon: "💧", label: "Water",     sub: "TWDB supply plans" },
];

function EmptyState() {
  return (
    <Flex h="full" align="center" justify="center" px={6}>
      <VStack gap={8} maxW="lg" w="full" textAlign="center">
        <VStack gap={2}>
          <Text fontSize="xl" fontWeight="semibold" color="gray.700">
            Ask about US infrastructure
          </Text>
          <Text fontSize="sm" color="gray.400">
            Live government data · Azure AI Search · GPT-4.1
          </Text>
        </VStack>

        <Grid templateColumns="repeat(2, 1fr)" gap={3} w="full">
          {DOMAINS.map((d) => (
            <Box
              key={d.label}
              bg="white"
              borderWidth="1px"
              borderColor="gray.200"
              borderRadius="xl"
              p={4}
              textAlign="left"
              boxShadow="xs"
            >
              <Text fontSize="xl" mb={1}>{d.icon}</Text>
              <Text fontSize="sm" fontWeight="semibold" color="gray.700">{d.label}</Text>
              <Text fontSize="xs" color="gray.400">{d.sub}</Text>
            </Box>
          ))}
        </Grid>
      </VStack>
    </Flex>
  );
}

// ── AI message avatar ─────────────────────────────────────────────────────────

function AIAvatar() {
  return (
    <Circle size="7" bg="blue.600" flexShrink={0} mt="1px">
      <Text fontSize="xs" fontWeight="bold" color="white">AI</Text>
    </Circle>
  );
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeCitations, setActiveCitations] = useState<Citation[]>([]);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function handleSuggestionSelect(text: string) {
    setInput(text);
    inputRef.current?.focus();
  }

  async function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    const query = input.trim();
    if (!query || loading) return;

    setInput("");
    setError(null);
    setLoading(true);

    const userMessage: Message = { role: "user", content: query, sources: [], citations: [], bridges: [] };
    setMessages((prev) => [...prev, userMessage]);
    trackQuerySubmitted(query.length);

    try {
      const resp: QueryResponse = await sendQuery(query);
      const bridges = extractBridgeData(resp.answer);
      if (bridges.length > 0) trackBridgeCardRendered(bridges.length);

      const citations = resp.sources.map((tool) => sourceToCitation(tool));
      const aiMessage: Message = {
        role: "assistant",
        content: resp.answer,
        sources: resp.sources,
        citations,
        bridges,
        traceId: resp.trace_id,
      };
      setMessages((prev) => [...prev, aiMessage]);
      setActiveCitations(aiMessage.citations);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  const shortSha = VERSION && VERSION !== "local" ? VERSION.slice(0, 7) : null;

  return (
    <Flex h="100vh" direction="column" bg="gray.50">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <Flex
        as="header"
        bg="white"
        borderBottomWidth="1px"
        borderColor="gray.200"
        px={5}
        py={3}
        align="center"
        justify="space-between"
        flexShrink={0}
      >
        <HStack gap={3}>
          <Flex
            w={8} h={8}
            bg="blue.600"
            borderRadius="lg"
            align="center"
            justify="center"
            flexShrink={0}
          >
            <Text fontSize="xs" fontWeight="bold" color="white" letterSpacing="tight">IA</Text>
          </Flex>
          <Box>
            <Text fontWeight="semibold" color="gray.800" fontSize="sm" lineHeight="shorter">
              InfraAdvisor AI
            </Text>
            <Text fontSize="xs" color="gray.400" lineHeight="shorter">
              Infrastructure advisory platform
            </Text>
          </Box>
        </HStack>

        <HStack gap={3}>
          <Badge colorPalette="green" variant="subtle" fontSize="xs" borderRadius="full" px={2}>
            Live
          </Badge>
          {shortSha ? (
            <Link
              href={`${REPO_URL}/commit/${VERSION}`}
              target="_blank"
              rel="noopener noreferrer"
              fontSize="xs"
              fontFamily="mono"
              color="gray.400"
              _hover={{ color: "blue.600", textDecoration: "none" }}
              display="flex"
              alignItems="center"
              gap={1}
            >
              {shortSha}
              <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M2 10L10 2M10 2H5M10 2v5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Link>
          ) : (
            <Link
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              fontSize="xs"
              color="gray.400"
              _hover={{ color: "blue.600", textDecoration: "none" }}
            >
              GitHub
            </Link>
          )}
        </HStack>
      </Flex>

      {/* ── Body ───────────────────────────────────────────────────────────── */}
      <Flex flex={1} minH={0} overflow="hidden">
        {/* Chat column */}
        <Flex direction="column" flex={1} minW={0}>
          {/* Message thread */}
          <Box flex={1} overflowY="auto" px={5} py={6}>
            {messages.length === 0 ? (
              <EmptyState />
            ) : (
              <VStack gap={5} align="stretch" maxW="3xl" mx="auto">
                {messages.map((msg, i) => (
                  <Flex
                    key={i}
                    data-testid={msg.role === "assistant" ? "ai-message" : "user-message"}
                    justify={msg.role === "user" ? "flex-end" : "flex-start"}
                    align="flex-start"
                    gap={2.5}
                  >
                    {msg.role === "assistant" && <AIAvatar />}

                    <Box
                      maxW={{ base: "90%", md: "2xl" }}
                      bg={msg.role === "user" ? "blue.600" : "white"}
                      color={msg.role === "user" ? "white" : "gray.800"}
                      borderRadius={msg.role === "user" ? "2xl" : "0 2xl 2xl 2xl"}
                      px={4}
                      py={3}
                      fontSize="sm"
                      lineHeight="tall"
                      boxShadow={msg.role === "assistant" ? "xs" : "none"}
                      borderWidth={msg.role === "assistant" ? "1px" : 0}
                      borderColor="gray.200"
                    >
                      <Text whiteSpace="pre-wrap">{msg.content}</Text>

                      {msg.bridges.length > 0 && (
                        <VStack mt={3} gap={2} align="stretch">
                          {msg.bridges.map((b, j) => <BridgeCard key={j} bridge={b} />)}
                        </VStack>
                      )}

                      {msg.sources.length > 0 && (
                        <>
                          <Separator my={2.5} borderColor={msg.role === "user" ? "blue.500" : "gray.100"} />
                          <HStack flexWrap="wrap" gap={1}>
                            {msg.sources.map((s) => (
                              <Badge
                                key={s}
                                variant="subtle"
                                colorPalette={msg.role === "user" ? "blue" : "gray"}
                                fontSize="xs"
                                borderRadius="full"
                                px={2}
                                py={0.5}
                              >
                                {TOOL_META[s]?.label ?? s}
                              </Badge>
                            ))}
                          </HStack>
                        </>
                      )}

                      {msg.traceId && (
                        <Text
                          mt={1.5}
                          fontSize="xs"
                          color={msg.role === "user" ? "blue.300" : "gray.400"}
                          fontFamily="mono"
                        >
                          trace: {msg.traceId}
                        </Text>
                      )}
                    </Box>
                  </Flex>
                ))}

                {loading && (
                  <Flex justify="flex-start" align="flex-start" gap={2.5} data-testid="loading-indicator">
                    <AIAvatar />
                    <Box
                      bg="white"
                      borderRadius="0 2xl 2xl 2xl"
                      px={4}
                      py={3}
                      boxShadow="xs"
                      borderWidth="1px"
                      borderColor="gray.200"
                    >
                      <HStack gap={2}>
                        <Spinner size="xs" color="blue.500" />
                        <Text fontSize="xs" color="gray.400">Thinking…</Text>
                      </HStack>
                    </Box>
                  </Flex>
                )}

                {error && (
                  <Alert.Root status="error" borderRadius="xl" fontSize="sm">
                    <Alert.Indicator />
                    <Alert.Description>{error}</Alert.Description>
                  </Alert.Root>
                )}

                <div ref={bottomRef} />
              </VStack>
            )}
          </Box>

          {/* ── Input bar ──────────────────────────────────────────────────── */}
          <Box
            borderTopWidth="1px"
            borderColor="gray.200"
            bg="white"
            px={5}
            pt={3}
            pb={4}
            flexShrink={0}
          >
            <VStack gap={2.5} align="stretch" maxW="3xl" mx="auto">
              <QuerySuggestions onSelect={handleSuggestionSelect} disabled={loading} />
              <HStack as="form" onSubmit={handleSubmit} gap={2} align="flex-end">
                <Textarea
                  ref={inputRef}
                  data-testid="chat-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask about bridges, disasters, energy, or water systems…"
                  rows={1}
                  disabled={loading}
                  resize="none"
                  borderRadius="xl"
                  borderColor="gray.300"
                  bg="gray.50"
                  _focus={{
                    borderColor: "blue.500",
                    bg: "white",
                    boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)",
                  }}
                  fontSize="sm"
                  py="10px"
                  flex={1}
                />
                <IconButton
                  type="submit"
                  data-testid="send-button"
                  aria-label="Send message"
                  disabled={loading || !input.trim()}
                  colorPalette="blue"
                  borderRadius="xl"
                  h="42px"
                  w="42px"
                  flexShrink={0}
                >
                  <SendIcon />
                </IconButton>
              </HStack>
              <Text fontSize="xs" color="gray.400" textAlign="center">
                Press <Text as="kbd" fontFamily="mono" px={1} bg="gray.100" borderRadius="sm">↵</Text> to send · <Text as="kbd" fontFamily="mono" px={1} bg="gray.100" borderRadius="sm">⇧↵</Text> for newline
              </Text>
            </VStack>
          </Box>
        </Flex>

        {/* ── Citation sidebar ───────────────────────────────────────────── */}
        <Box
          w="72"
          borderLeftWidth="1px"
          borderColor="gray.200"
          bg="white"
          p={4}
          overflowY="auto"
          flexShrink={0}
          display={{ base: "none", lg: "block" }}
        >
          <CitationPanel citations={activeCitations} />
        </Box>
      </Flex>
    </Flex>
  );
}
