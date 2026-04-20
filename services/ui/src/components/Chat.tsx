import React, { useEffect, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Flex,
  HStack,
  IconButton,
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

interface Message {
  role: "user" | "assistant";
  content: string;
  sources: string[];
  citations: Citation[];
  bridges: BridgeData[];
  traceId?: string | null;
}

function SendIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14m-7-7l7 7-7 7" />
    </svg>
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

      const aiMessage: Message = {
        role: "assistant",
        content: resp.answer,
        sources: resp.sources,
        citations: [],
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

  return (
    <Flex h="100vh" direction="column" bg="gray.50">
      {/* Header */}
      <Flex
        as="header"
        bg="blue.700"
        px={6}
        py={3}
        align="center"
        justify="space-between"
        flexShrink={0}
        boxShadow="md"
      >
        <HStack gap={3}>
          <Flex w={8} h={8} bg="white" borderRadius="lg" align="center" justify="center" flexShrink={0}>
            <Text fontSize="sm" fontWeight="bold" color="blue.700">IA</Text>
          </Flex>
          <Box>
            <Text fontWeight="semibold" color="white" fontSize="md" lineHeight="short">
              InfraAdvisor AI
            </Text>
            <Text fontSize="xs" color="blue.200">
              Bridge · Water · Energy · Disaster
            </Text>
          </Box>
        </HStack>
        <Badge colorPalette="green" variant="solid" fontSize="xs" borderRadius="full" px={2}>
          Live
        </Badge>
      </Flex>

      {/* Body */}
      <Flex flex={1} minH={0} overflow="hidden">
        {/* Chat column */}
        <Flex direction="column" flex={1} minW={0}>
          {/* Message thread */}
          <Box flex={1} overflowY="auto" px={4} py={5} className="message-thread">
            {messages.length === 0 && (
              <Flex h="full" align="center" justify="center">
                <Text fontSize="sm" color="gray.400" textAlign="center" maxW="sm" px={4}>
                  Ask about bridges, water systems, energy infrastructure, or disaster history across US regions.
                </Text>
              </Flex>
            )}

            <VStack gap={4} align="stretch">
              {messages.map((msg, i) => (
                <Flex
                  key={i}
                  data-testid={msg.role === "assistant" ? "ai-message" : "user-message"}
                  justify={msg.role === "user" ? "flex-end" : "flex-start"}
                >
                  <Box
                    maxW={{ base: "90%", md: "2xl" }}
                    bg={msg.role === "user" ? "blue.600" : "white"}
                    color={msg.role === "user" ? "white" : "gray.800"}
                    borderRadius="2xl"
                    px={4}
                    py={3}
                    fontSize="sm"
                    lineHeight="tall"
                    boxShadow={msg.role === "assistant" ? "sm" : "none"}
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
                      <HStack mt={2} flexWrap="wrap" gap={1}>
                        {msg.sources.map((s) => (
                          <Badge key={s} variant="subtle" colorPalette="gray" fontSize="xs" borderRadius="full">
                            {s}
                          </Badge>
                        ))}
                      </HStack>
                    )}

                    {msg.traceId && (
                      <Text
                        mt={1}
                        fontSize="xs"
                        color={msg.role === "user" ? "blue.200" : "gray.400"}
                        fontFamily="mono"
                      >
                        trace: {msg.traceId}
                      </Text>
                    )}
                  </Box>
                </Flex>
              ))}

              {loading && (
                <Flex justify="flex-start" data-testid="loading-indicator">
                  <Box
                    bg="white"
                    borderRadius="2xl"
                    px={4}
                    py={3}
                    boxShadow="sm"
                    borderWidth="1px"
                    borderColor="gray.200"
                  >
                    <Spinner size="sm" color="blue.500" />
                  </Box>
                </Flex>
              )}

              {error && (
                <Alert.Root status="error" borderRadius="lg" fontSize="sm">
                  <Alert.Indicator />
                  <Alert.Description>{error}</Alert.Description>
                </Alert.Root>
              )}

              <div ref={bottomRef} />
            </VStack>
          </Box>

          {/* Input bar */}
          <Box
            borderTopWidth="1px"
            borderColor="gray.200"
            bg="white"
            px={4}
            py={3}
            flexShrink={0}
          >
            <VStack gap={2} align="stretch">
              <QuerySuggestions onSelect={handleSuggestionSelect} disabled={loading} />
              <HStack as="form" onSubmit={handleSubmit} gap={2} align="flex-end">
                <Textarea
                  ref={inputRef}
                  data-testid="chat-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Ask about bridges, disasters, energy..."
                  rows={1}
                  disabled={loading}
                  resize="none"
                  borderRadius="xl"
                  borderColor="gray.300"
                  _focus={{ borderColor: "blue.500", boxShadow: "0 0 0 1px var(--chakra-colors-blue-500)" }}
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
                  flexShrink={0}
                >
                  <SendIcon />
                </IconButton>
              </HStack>
            </VStack>
          </Box>
        </Flex>

        {/* Citation panel — right sidebar */}
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
