import React, { useEffect, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Button,
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
import ReactMarkdown from "react-markdown";
import { BridgeData, Citation, QueryResponse, SuggestionItem, extractBridgeData, fetchSuggestions, sendQuery } from "../lib/api";
import {
  trackBridgeCardRendered,
  trackMessageCopied,
  trackMessageFeedback,
  trackMessageReported,
  trackQuerySubmitted,
} from "../lib/datadog-rum";
import { AboutModal } from "./AboutModal";
import { BridgeCard } from "./BridgeCard";
import { CitationPanel } from "./CitationPanel";
import { QuerySuggestions } from "./QuerySuggestions";
import { Sandbox } from "./Sandbox";

type Suggestion = SuggestionItem;

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
  get_bridge_condition:       { label: "Bridge Condition",      document_type: "Bridge",   description: "FHWA National Bridge Inventory" },
  get_disaster_history:       { label: "Disaster History",      document_type: "Disaster", description: "OpenFEMA disaster declarations" },
  get_energy_infrastructure:  { label: "Energy Infrastructure", document_type: "Energy",   description: "EIA energy infrastructure data" },
  get_water_infrastructure:   { label: "Water Infrastructure",  document_type: "Water",    description: "Texas Water Development Board plans" },
  get_ercot_energy_storage:   { label: "ERCOT Storage",         document_type: "Energy",          description: "ERCOT Texas grid energy storage data" },
  search_txdot_open_data:     { label: "TxDOT Open Data",       document_type: "Transportation",  description: "TxDOT traffic counts and construction projects" },
  search_project_knowledge:   { label: "Knowledge Base",        document_type: "Document",        description: "Azure AI Search hybrid index" },
  draft_document:             { label: "Draft Document",        document_type: "Document", description: "Jinja2 consulting document template" },
};

// ── Initial suggestion pills (shown before first message) ─────────────────────

const INITIAL_SUGGESTIONS: Suggestion[] = [
  {
    label: "🌉 Deficient Texas bridges",
    query: "Pull all structurally deficient bridges in Texas with ADT over 10,000 and last inspection before 2022.",
  },
  {
    label: "💧 SDWA violations",
    query: "Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?",
  },
  {
    label: "🗺️ Corpus Christi water plan",
    query: "What water supply projects are recommended for the Corpus Christi region in the TWDB 2026 State Water Plan?",
  },
  {
    label: "⚡ Southeast grid resilience",
    query: "Compare grid resilience investment patterns across southeastern states since 2018 using EIA data.",
  },
];

// ── Domain-specific follow-ups keyed by MCP tool name ────────────────────────

const FOLLOW_UPS_BY_TOOL: Record<string, Suggestion[]> = {
  get_bridge_condition: [
    { label: "🌉 Poor-rated bridges",     query: "Show all Texas bridges rated poor or below on the NBI structural evaluation scale." },
    { label: "🌉 High-traffic deficient", query: "List structurally deficient Texas bridges with ADT over 20,000 vehicles per day." },
    { label: "🌉 Inspection backlog",     query: "Which Texas bridges have not been inspected in more than 4 years per NBI records?" },
    { label: "🌉 Rehab cost estimate",    query: "What is the estimated cost to rehabilitate all structurally deficient bridges in Texas?" },
  ],
  get_disaster_history: [
    { label: "🌊 Recent declarations",  query: "What major disaster declarations have occurred in Texas in the last 3 years?" },
    { label: "🌊 Hurricane risk zones", query: "Which Texas counties have the highest frequency of hurricane disaster declarations?" },
    { label: "🌊 Flood damage history", query: "Summarize FEMA flood disaster declarations in Texas since 2015 by county." },
    { label: "🌊 Mitigation grants",    query: "What FEMA hazard mitigation grants are available for Texas infrastructure projects?" },
  ],
  get_energy_infrastructure: [
    { label: "⚡ Grid vulnerabilities",  query: "What are the key grid vulnerability points identified in EIA data for Texas?" },
    { label: "⚡ Renewable capacity",    query: "What is the current renewable energy generation capacity in Texas according to EIA?" },
    { label: "⚡ Post-2021 resilience",  query: "How has Texas improved grid resilience since the 2021 winter storm based on EIA data?" },
    { label: "⚡ Aging power plants",    query: "What percentage of Texas power plants are more than 30 years old per EIA records?" },
  ],
  get_water_infrastructure: [
    { label: "💧 Supply gap projections", query: "What water supply gaps are projected for Texas in the 2026 State Water Plan?" },
    { label: "💧 Conservation strategies", query: "What conservation strategies are recommended in the TWDB 2026 water plan?" },
    { label: "💧 Project cost estimates", query: "What is the total estimated cost of recommended water projects in the TWDB plan?" },
    { label: "💧 Drought risk regions",  query: "Which Texas regions face the highest drought risk according to TWDB projections?" },
  ],
  search_project_knowledge: [
    { label: "📄 Similar projects",   query: "Find consulting projects in our knowledge base similar to this infrastructure type." },
    { label: "📄 Risk frameworks",    query: "What risk assessment frameworks does our knowledge base recommend for this project type?" },
    { label: "📄 Federal funding",    query: "What federal funding sources are available for this type of infrastructure project?" },
    { label: "📄 Case studies",       query: "Show case studies from our knowledge base for similar completed infrastructure projects." },
  ],
  draft_document: [
    { label: "📝 Cost estimate",   query: "Generate a cost estimate scaffold for this infrastructure project." },
    { label: "📝 Risk summary",    query: "Draft a risk summary memo for this infrastructure assessment." },
    { label: "📝 Funding memo",    query: "Create a federal funding positioning memo for this project." },
    { label: "📝 Scope of work",   query: "Draft a scope of work document for this infrastructure improvement project." },
  ],
};

/** Derive 4 follow-up suggestions from the MCP tools used in a response. */
function getFollowUpSuggestions(sources: string[]): Suggestion[] {
  for (const src of sources) {
    const follow = FOLLOW_UPS_BY_TOOL[src];
    if (follow) return follow;
  }
  return INITIAL_SUGGESTIONS;
}

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

// ── Inline SVG icons for action buttons ───────────────────────────────────────

function ThumbUpIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor">
      <path d="M6.956 1.745C7.021.81 7.908.087 8.864.325l.261.066c.463.116.874.456 1.012.965.22.816.533 2.511.062 4.51a19.52 19.52 0 0 1 1.365-.122c.485 0 .965.042 1.42.124 1.072.188 1.799 1.27 1.442 2.317-.48 1.4-1.058 2.523-1.602 3.343-1.07 1.603-2.703 1.888-4.218 1.613a15.7 15.7 0 0 1-1.316-.31A4.01 4.01 0 0 1 5.002 14H2.5a.5.5 0 0 1-.5-.5v-6a.5.5 0 0 1 .5-.5h.463c.535 0 1.02-.28 1.316-.75l2.677-4.51z"/>
    </svg>
  );
}

function ThumbDownIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor">
      <path d="M6.956 14.534c.065.936.952 1.659 1.908 1.42l.261-.065a1.378 1.378 0 0 0 1.012-.965c.22-.816.533-2.512.062-4.51.205.031.412.05.621.062.285.006.57-.01.848-.048 1.272-.194 2.048-1.221 1.692-2.272-.48-1.399-1.058-2.523-1.601-3.343-1.07-1.604-2.703-1.888-4.218-1.613a15.67 15.67 0 0 0-1.316.31A4.01 4.01 0 0 0 4.999 2H2.5a.5.5 0 0 0-.5.5v6a.5.5 0 0 0 .5.5h.463c.535 0 1.02.279 1.316.75l2.677 4.284z"/>
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4" width="9" height="11" rx="1.5" />
      <path d="M3 11H2.5A1.5 1.5 0 0 1 1 9.5v-7A1.5 1.5 0 0 1 2.5 1h7A1.5 1.5 0 0 1 11 2.5V3" />
    </svg>
  );
}

function FlagIcon() {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 14V2" />
      <path d="M2 2h8l-1.5 3L10 8H2" />
    </svg>
  );
}

// ── Per-message action bar (shown below AI messages) ─────────────────────────

interface MessageActionsProps {
  content: string;
  domain?: string;
}

function MessageActions({ content, domain }: MessageActionsProps) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);

  function handleCopy() {
    navigator.clipboard.writeText(content).then(() => {
      setCopied(true);
      trackMessageCopied(domain);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  function handleFeedback(positive: boolean) {
    const next = positive ? "up" : "down";
    if (feedback === next) return;
    setFeedback(next);
    trackMessageFeedback(positive, domain);
  }

  function handleReport() {
    trackMessageReported(domain);
    // visual ack could be added here
  }

  const actionBtnProps = {
    size: "xs" as const,
    variant: "ghost" as const,
    colorPalette: "gray",
    borderRadius: "md",
    h: "22px",
    w: "22px",
    minW: "22px",
  };

  return (
    <HStack gap={0.5} mt={1.5}>
      <IconButton
        {...actionBtnProps}
        aria-label="Helpful"
        title="Helpful"
        color={feedback === "up" ? "green.500" : "gray.400"}
        onClick={() => handleFeedback(true)}
      >
        <ThumbUpIcon />
      </IconButton>
      <IconButton
        {...actionBtnProps}
        aria-label="Not helpful"
        title="Not helpful"
        color={feedback === "down" ? "red.500" : "gray.400"}
        onClick={() => handleFeedback(false)}
      >
        <ThumbDownIcon />
      </IconButton>
      <IconButton
        {...actionBtnProps}
        aria-label={copied ? "Copied!" : "Copy markdown"}
        title={copied ? "Copied!" : "Copy markdown"}
        color={copied ? "blue.500" : "gray.400"}
        onClick={handleCopy}
      >
        <CopyIcon />
      </IconButton>
      <IconButton
        {...actionBtnProps}
        aria-label="Report issue"
        title="Report issue"
        color="gray.400"
        onClick={handleReport}
      >
        <FlagIcon />
      </IconButton>
    </HStack>
  );
}

// ── Markdown renderer for AI responses ───────────────────────────────────────

function MarkdownContent({ content }: { content: string }) {
  return (
    <Box
      fontSize="sm"
      lineHeight="tall"
      css={{
        "& p": { marginBottom: "0.5em" },
        "& p:last-child": { marginBottom: 0 },
        "& ul, & ol": { paddingLeft: "1.25em", marginBottom: "0.5em" },
        "& li": { marginBottom: "0.15em" },
        "& strong": { fontWeight: 600 },
        "& h1, & h2, & h3": { fontWeight: 600, marginBottom: "0.4em", marginTop: "0.6em" },
        "& h1": { fontSize: "1em" },
        "& h2": { fontSize: "0.95em" },
        "& h3": { fontSize: "0.9em" },
        "& code": {
          fontFamily: "mono",
          fontSize: "0.85em",
          background: "var(--chakra-colors-gray-100)",
          borderRadius: "3px",
          padding: "0 3px",
        },
        "& pre": {
          background: "var(--chakra-colors-gray-50)",
          border: "1px solid var(--chakra-colors-gray-200)",
          borderRadius: "6px",
          padding: "0.75em",
          overflowX: "auto",
          marginBottom: "0.5em",
        },
        "& pre code": { background: "none", padding: 0 },
        "& blockquote": {
          borderLeft: "3px solid var(--chakra-colors-gray-200)",
          paddingLeft: "0.75em",
          color: "var(--chakra-colors-gray-500)",
          margin: "0.5em 0",
        },
      }}
    >
      <ReactMarkdown>{content}</ReactMarkdown>
    </Box>
  );
}

export function Chat() {
  const [activeView, setActiveView] = useState<"chat" | "sandbox">("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeCitations, setActiveCitations] = useState<Citation[]>([]);
  const [recommendations, setRecommendations] = useState<Suggestion[]>(INITIAL_SUGGESTIONS);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Core submit logic — accepts query directly so pills can auto-submit without
  // relying on `input` state (avoids React batching issues).
  async function submit(query: string) {
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
      // Show static domain follow-ups immediately, then upgrade to LLM-generated ones
      setRecommendations(getFollowUpSuggestions(resp.sources));
      fetchSuggestions(query, resp.answer, resp.sources)
        .then((items) => { if (items.length > 0) setRecommendations(items); })
        .catch(() => { /* keep static fallback already shown */ });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  // Pill click — auto-submits immediately (no manual send needed for Synthetics)
  function handleSuggestionSelect(text: string) {
    submit(text);
  }

  async function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    await submit(input.trim());
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
          <img src="/favicon.svg" width={32} height={32} alt="InfraAdvisor AI" style={{ flexShrink: 0 }} />
          <Box>
            <Text fontWeight="semibold" color="gray.800" fontSize="sm" lineHeight="shorter">
              InfraAdvisor AI
            </Text>
            <Text fontSize="xs" color="gray.400" lineHeight="shorter">
              Infrastructure advisory platform
            </Text>
          </Box>
        </HStack>

        {/* ── View tabs ──────────────────────────────────────────────────── */}
        <HStack gap={0.5} bg="gray.100" borderRadius="lg" p={0.5}>
          {(["chat", "sandbox"] as const).map((view) => (
            <Button
              key={view}
              size="xs"
              variant={activeView === view ? "solid" : "ghost"}
              colorPalette={activeView === view ? "blue" : "gray"}
              borderRadius="md"
              fontWeight={activeView === view ? "semibold" : "normal"}
              fontSize="xs"
              px={3}
              h="24px"
              onClick={() => setActiveView(view)}
              textTransform="capitalize"
            >
              {view === "chat" ? "Chat" : "Sandbox"}
            </Button>
          ))}
        </HStack>

        <HStack gap={2}>
          <AboutModal />
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
      {activeView === "sandbox" && <Sandbox />}
      <Flex flex={1} minH={0} overflow="hidden" display={activeView === "chat" ? "flex" : "none"}>
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

                    <Box maxW={{ base: "90%", md: "2xl" }}>
                      <Box
                        bg={msg.role === "user" ? "blue.600" : "white"}
                        color={msg.role === "user" ? "white" : "gray.800"}
                        borderRadius={msg.role === "user" ? "2xl" : "0 2xl 2xl 2xl"}
                        px={4}
                        py={3}
                        boxShadow={msg.role === "assistant" ? "xs" : "none"}
                        borderWidth={msg.role === "assistant" ? "1px" : 0}
                        borderColor="gray.200"
                      >
                        {msg.role === "assistant" ? (
                          <MarkdownContent content={msg.content} />
                        ) : (
                          <Text fontSize="sm" lineHeight="tall" whiteSpace="pre-wrap">{msg.content}</Text>
                        )}

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

                      {msg.role === "assistant" && (
                        <MessageActions content={msg.content} />
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
              <QuerySuggestions suggestions={recommendations} onSelect={handleSuggestionSelect} disabled={loading} />
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
