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
  NativeSelect,
  Text,
  Textarea,
  VStack,
} from "@chakra-ui/react";
import ReactMarkdown from "react-markdown";
import { ThumbsUp, ThumbsDown, Copy, Flag, SendHorizontal, Gauge, HardHat, ShieldCheck, Briefcase, Compass, ExternalLink, ChartNoAxesGantt, ChevronLeft } from "lucide-react";
import { hasSeenTour, startTour } from "../lib/tour";
import { ApiError, BackendType, BridgeData, ConversationDetail, ConversationSummary, FeedbackRating, StreamEvent, SuggestionItem, createConversation, deleteConversation, extractBridgeData, fetchInitialSuggestions, fetchModels, fetchSuggestions, getBackend, getConversation, getModel, newConversation, sendQueryStream, setBackend, setModel, setSessionId, submitFeedback } from "../lib/api";
import {
  trackBridgeCardRendered,
  trackMessageCopied,
  trackMessageFeedback,
  trackMessageReported,
  trackQuerySubmitted,
} from "../lib/datadog-rum";
import { AboutModal } from "./AboutModal";
import { AdminTab } from "./AdminTab";
import { BridgeCard } from "./BridgeCard";
import { StepKind, StepStatus, ToolStepChip } from "./ToolStepChip";
import { ConversationSidebar } from "./ConversationSidebar";
import { QuerySuggestions } from "./QuerySuggestions";
import { Sandbox } from "./Sandbox";
import { useAuth } from "../hooks/useAuth";

type Suggestion = SuggestionItem;

const VERSION = import.meta.env.VITE_APP_VERSION as string | undefined;
const REPO_URL = "https://github.com/kyletaylored/infra-advisor-ai";

// One live step in the agent loop — shown as a ToolStepChip above the
// message content. The id is stable across the running → ok/error
// transitions so we update the same chip in place.
export interface StreamStep {
  id: string;                       // tool call id or internal step name
  kind: StepKind;
  status: StepStatus;
  resultSummary?: string | null;
  durationMs?: number;
  argsJson?: string | null;
  sources?: string[];
}

interface Message {
  role: "user" | "assistant";
  content: string;
  sources: string[];
  steps: StreamStep[];              // tool / pipeline steps; rendered above content
  bridges: BridgeData[];
  traceId?: string | null;
  spanId?: string | null;
}

const TOOL_META: Record<string, { label: string; document_type: string; description: string; source_url?: string; data_notes?: string }> = {
  get_bridge_condition: {
    label: "Bridge Condition", document_type: "Bridge",
    description: "FHWA National Bridge Inventory — structural ratings, sufficiency scores, scour flags, ADT",
    source_url: "https://www.fhwa.dot.gov/bridge/nbi.cfm",
    data_notes: "Coverage: all US public bridges. Key fields: sufficiency rating (0–100), deck/superstructure/substructure condition (0–9), scour critical flag.",
  },
  get_disaster_history: {
    label: "Disaster History", document_type: "Disaster",
    description: "OpenFEMA disaster declarations and public assistance data",
    source_url: "https://www.fema.gov/openfema-data-page/disaster-declarations-summaries-v2",
    data_notes: "Coverage: all US FEMA major disaster declarations. Filterable by state, county, incident type, and date range.",
  },
  get_energy_infrastructure: {
    label: "Energy Infrastructure", document_type: "Energy",
    description: "EIA electricity generation and capacity by state, fuel type, and year",
    source_url: "https://www.eia.gov/electricity/data.php",
    data_notes: "Coverage: all US states. Returns generation (MWh) and capacity (MW) by fuel type. Does not include cost, age, or investment data.",
  },
  get_water_infrastructure: {
    label: "Water Infrastructure", document_type: "Water",
    description: "EPA SDWIS drinking water compliance + TWDB 2026 State Water Plan projects",
    source_url: "https://www.epa.gov/enviro/sdwis-search",
    data_notes: "SDWIS coverage: all US public water systems. TWDB coverage: Texas only — requires knowledge_base_init DAG to be run.",
  },
  get_ercot_energy_storage: {
    label: "ERCOT Storage", document_type: "Energy",
    description: "ERCOT Texas grid energy storage resource (ESR) charging data",
    source_url: "https://www.ercot.com/gridinfo/resource",
    data_notes: "Coverage: Texas ERCOT grid only (~90% of Texas). Returns 4-second interval ESR charging/discharging data.",
  },
  search_txdot_open_data: {
    label: "TxDOT Open Data", document_type: "Transportation",
    description: "TxDOT Open Data portal — AADT traffic counts, construction projects, highway datasets",
    source_url: "https://gis-txdot.opendata.arcgis.com/",
    data_notes: "Coverage: Texas only. Datasets include annual average daily traffic (AADT), active construction projects, and highway geometry.",
  },
  search_project_knowledge: {
    label: "Knowledge Base", document_type: "Document",
    description: "Firm internal knowledge base — case studies, risk frameworks, templates (Azure AI Search)",
    data_notes: "Requires knowledge_base_init Airflow DAG to be run. Scope depends on indexed documents.",
  },
  draft_document: {
    label: "Draft Document", document_type: "Document",
    description: "Generate structured consulting document scaffolds (SOW, risk summary, cost estimate, funding memo)",
    data_notes: "Templates are generated by the LLM using firm knowledge base context. Always review before client delivery.",
  },
  get_procurement_opportunities: {
    label: "Federal Opportunities", document_type: "Procurement",
    description: "SAM.gov and grants.gov — active federal contract opportunities and open grant programs",
    source_url: "https://sam.gov/content/opportunities",
    data_notes: "SAM.gov: active solicitations. grants.gov: open grant programs. Results merged and sorted by deadline. Requires SAMGOV_API_KEY.",
  },
  get_contract_awards: {
    label: "Contract Awards", document_type: "Procurement",
    description: "USASpending.gov — historical federal contract awards for competitive intelligence",
    source_url: "https://www.usaspending.gov/search",
    data_notes: "Coverage: all federal contract awards. Returns recipient, award amount, agency, NAICS code. No auth required.",
  },
  search_web_procurement: {
    label: "Web Procurement", document_type: "Procurement",
    description: "State and local RFPs, bond elections, and government budget announcements via Azure OpenAI web search",
    data_notes: "Searches .gov and .us domains plus procurement portals. Confidence field indicates extraction reliability — flag medium-confidence results to users.",
  },
};

// ── Static fallback suggestions (used when API is unavailable) ───────────────

const INITIAL_SUGGESTIONS: Suggestion[] = [
  {
    label: "Deficient bridges",
    query: "List structurally deficient bridges in Texas with ADT over 10,000, sorted by sufficiency rating.",
  },
  {
    label: "SDWA violations",
    query: "Which Texas community water systems have open Safe Drinking Water Act violations serving more than 10,000 people?",
  },
  {
    label: "Infrastructure opportunities",
    query: "What active federal infrastructure procurement opportunities are open on SAM.gov for civil engineering NAICS codes?",
  },
  {
    label: "Disaster risk counties",
    query: "Which Texas counties have received 5 or more FEMA disaster declarations since 2010?",
  },
];

// ── Domain-specific follow-ups keyed by MCP tool name ────────────────────────

const FOLLOW_UPS_BY_TOOL: Record<string, Suggestion[]> = {
  get_bridge_condition: [
    { label: "Poor-rated bridges", query: "Show all Texas bridges rated poor or below on the NBI structural evaluation scale, sorted by sufficiency rating." },
    { label: "High-traffic deficient", query: "List structurally deficient Texas bridges with ADT over 20,000 vehicles per day and flag any scour-critical ones." },
    { label: "Scour-critical bridges", query: "Which Texas bridges are flagged as scour-critical in NBI records and what are their sufficiency ratings?" },
    { label: "Lowest sufficiency ratings", query: "What are the 10 Texas bridges with the lowest NBI sufficiency ratings and what condition scores do they have?" },
  ],
  get_disaster_history: [
    { label: "Recent declarations", query: "What major disaster declarations have occurred in Texas in the last 3 years?" },
    { label: "Hurricane risk zones", query: "Which Texas counties have the highest frequency of hurricane disaster declarations?" },
    { label: "Flood damage history", query: "Summarize FEMA flood disaster declarations in Texas since 2015 by county." },
    { label: "Repeat disaster counties", query: "Which Texas counties have received 5 or more FEMA disaster declarations since 2010?" },
  ],
  get_energy_infrastructure: [
    { label: "Renewable capacity", query: "What is the current renewable energy generation capacity in Texas according to EIA — break down by wind, solar, and hydro." },
    { label: "Fuel mix trends", query: "How has Texas electricity generation shifted between natural gas, wind, and solar since 2018 based on EIA data?" },
    { label: "Post-2021 generation", query: "How did Texas natural gas and wind generation capacity change between 2020 and 2023 in EIA data?" },
    { label: "State capacity comparison", query: "Compare total electricity generation capacity across Texas, Louisiana, and Oklahoma by fuel type using EIA data." },
  ],
  get_water_infrastructure: [
    { label: "Supply gap projections", query: "What water supply gaps are projected for Texas in the 2026 State Water Plan?" },
    { label: "Conservation strategies", query: "What conservation strategies are recommended in the TWDB 2026 water plan?" },
    { label: "Project cost estimates", query: "What is the total estimated cost of recommended water projects in the TWDB plan?" },
    { label: "Drought risk regions", query: "Which Texas regions face the highest drought risk according to TWDB projections?" },
  ],
  search_project_knowledge: [
    { label: "Similar projects", query: "Find consulting projects in our knowledge base similar to this infrastructure type." },
    { label: "Risk frameworks", query: "What risk assessment frameworks does our knowledge base recommend for this project type?" },
    { label: "Federal funding", query: "What federal funding sources are available for this type of infrastructure project?" },
    { label: "Case studies", query: "Show case studies from our knowledge base for similar completed infrastructure projects." },
  ],
  draft_document: [
    { label: "Cost estimate", query: "Generate a cost estimate scaffold for this infrastructure project." },
    { label: "Risk summary", query: "Draft a risk summary memo for this infrastructure assessment." },
    { label: "Funding memo", query: "Create a federal funding positioning memo for this project." },
    { label: "Scope of work", query: "Draft a scope of work document for this infrastructure improvement project." },
  ],
  get_procurement_opportunities: [
    { label: "Award benchmarks", query: "Who are the incumbent contractors and what are the pricing benchmarks for similar federal infrastructure awards in this NAICS code?" },
    { label: "Grant deadlines", query: "What infrastructure grant programs have upcoming application deadlines in the next 90 days?" },
    { label: "Agency spend profile", query: "Which federal agencies have spent the most on civil infrastructure contracts in the past 12 months?" },
    { label: "Win positioning", query: "Draft a capture strategy memo positioning our firm for this opportunity based on the competitive landscape." },
  ],
  get_contract_awards: [
    { label: "Competitor analysis", query: "Which firms are winning the most federal infrastructure awards in this NAICS code and what are their average contract sizes?" },
    { label: "Price benchmarks", query: "What is the price range for similar scope infrastructure awards from this agency in the last 2 years?" },
    { label: "Open opportunities", query: "What active federal solicitations exist for similar infrastructure work that we could pursue?" },
    { label: "BD memo", query: "Draft a competitive intelligence memo summarizing the market landscape for this project type." },
  ],
  search_web_procurement: [
    { label: "Bond elections", query: "What infrastructure bond elections are scheduled in Texas in the next 6 months?" },
    { label: "State RFPs", query: "What state agency RFPs are currently open for engineering or construction services?" },
    { label: "Similar projects", query: "Find recent local government RFPs for similar infrastructure rehabilitation work." },
    { label: "Pursuit memo", query: "Draft a go/no-go recommendation memo for pursuing this procurement opportunity." },
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

// Reconstruct lightweight tool chips from a list of tool names persisted on
// a historical message. We don't have arg / timing / result_summary for
// these — just a name → status:ok chip so the user sees which tools were
// used without losing the visual continuity with live streams.
function toStepsFromSources(sources: string[]): StreamStep[] {
  return sources.map((tool) => ({
    id: `historical:${tool}`,
    kind: { kind: "tool" as const, toolName: tool },
    status: "ok" as const,
  }));
}

// ── Domain tiles shown in the empty state ────────────────────────────────────

const DOMAINS = [
  {
    Icon: Gauge,
    label: "Engineering",
    sub: "Infrastructure assessment",
    query: "List structurally deficient bridges in Texas with ADT over 10,000, sorted by sufficiency rating — include scour-critical flags.",
    color: "blue",
  },
  {
    Icon: HardHat,
    label: "Construction",
    sub: "Procurement & project data",
    query: "What active federal infrastructure construction opportunities are open on SAM.gov in Texas for highway and bridge NAICS codes?",
    color: "orange",
  },
  {
    Icon: ShieldCheck,
    label: "Resilience",
    sub: "Risk & disaster analysis",
    query: "Which Texas counties have received 5 or more FEMA disaster declarations since 2010, and what hazard types are most frequent?",
    color: "red",
  },
  {
    Icon: Briefcase,
    label: "Advisory",
    sub: "Docs, BD & firm knowledge",
    query: "Search our knowledge base for risk assessment frameworks and case studies relevant to aging infrastructure rehabilitation projects.",
    color: "purple",
  },
] as const;

function EmptyState({ onSelect }: { onSelect: (query: string) => void }) {
  return (
    <Flex h="full" align="center" justify="center" px={6}>
      <VStack gap={8} maxW="lg" w="full" textAlign="center">
        <VStack gap={2}>
          <Text fontSize="xl" fontWeight="semibold" color="gray.700">
            Infrastructure advisory at your fingertips
          </Text>
          <Text fontSize="sm" color="gray.400">
            Architecture · Engineering · Construction · Operations · Management
          </Text>
        </VStack>

        <Grid templateColumns="repeat(2, 1fr)" gap={3} w="full" data-tour="domain-tiles">
          {DOMAINS.map((d) => (
            <Box
              key={d.label}
              as="button"
              bg="white"
              borderWidth="1px"
              borderColor="gray.200"
              borderRadius="xl"
              p={4}
              textAlign="left"
              boxShadow="xs"
              cursor="pointer"
              transition="all 0.15s ease"
              _hover={{
                borderColor: `${d.color}.300`,
                boxShadow: "sm",
                bg: `${d.color}.50`,
                transform: "translateY(-1px)",
              }}
              _active={{ transform: "translateY(0px)", boxShadow: "xs" }}
              onClick={() => onSelect(d.query)}
            >
              <Box color={`${d.color}.500`} mb={2.5}>
                <d.Icon size={20} strokeWidth={1.5} />
              </Box>
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

// ── Per-message action bar (shown below AI messages) ─────────────────────────

interface MessageActionsProps {
  content: string;
  domain?: string;
  traceId?: string | null;
  spanId?: string | null;
}

function MessageActions({ content, domain, traceId, spanId }: MessageActionsProps) {
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
    if (traceId && spanId) {
      const rating: FeedbackRating = positive ? "positive" : "negative";
      submitFeedback(traceId, spanId, rating);
    }
  }

  function handleReport() {
    trackMessageReported(domain);
    if (traceId && spanId) {
      submitFeedback(traceId, spanId, "reported");
    }
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
        <ThumbsUp size={13} />
      </IconButton>
      <IconButton
        {...actionBtnProps}
        aria-label="Not helpful"
        title="Not helpful"
        color={feedback === "down" ? "red.500" : "gray.400"}
        onClick={() => handleFeedback(false)}
      >
        <ThumbsDown size={13} />
      </IconButton>
      <IconButton
        {...actionBtnProps}
        aria-label={copied ? "Copied!" : "Copy markdown"}
        title={copied ? "Copied!" : "Copy markdown"}
        color={copied ? "blue.500" : "gray.400"}
        onClick={handleCopy}
      >
        <Copy size={13} />
      </IconButton>
      <IconButton
        {...actionBtnProps}
        aria-label="Report issue"
        title="Report issue"
        color="gray.400"
        onClick={handleReport}
      >
        <Flag size={13} />
      </IconButton>
      {traceId && (
        <Link
          href={`https://us3.datadoghq.com/apm/trace/${traceId}${spanId ? `?spanID=${spanId}` : ""}`}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="View APM trace in Datadog"
          title={`View trace ${traceId} in Datadog APM`}
          display="inline-flex"
          alignItems="center"
          justifyContent="center"
          h="22px"
          w="22px"
          borderRadius="md"
          color="gray.400"
          _hover={{ color: "purple.500", bg: "gray.100" }}
          flexShrink={0}
        >
          <ChartNoAxesGantt size={13} />
        </Link>
      )}
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
  const { user, logout } = useAuth();
  const [activeView, setActiveView] = useState<"chat" | "sandbox" | "admin">("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ message: string; traceId: string | null } | null>(null);
  const [activeMsgIdx, setActiveMsgIdx] = useState<number | null>(null);
  const [recommendations, setRecommendations] = useState<Suggestion[]>([]);
  const [availableModels, setAvailableModels] = useState<string[]>(["gpt-4.1-mini"]);
  const [selectedModel, setSelectedModel] = useState<string>(getModel());
  const [selectedBackend, setSelectedBackend] = useState<BackendType>(getBackend());
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [sidebarRefresh, setSidebarRefresh] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-start tour on first visit
  useEffect(() => {
    if (!hasSeenTour()) {
      const t = setTimeout(() => startTour(), 600);
      return () => clearTimeout(t);
    }
  }, []);

  useEffect(() => {
    fetchModels().then((data) => {
      setAvailableModels(data.models);
      const stored = getModel();
      setSelectedModel(data.models.includes(stored) ? stored : data.default);
    });
  }, []);

  // Fetch LLM-generated initial suggestions spanning AEC/O&M practice areas
  useEffect(() => {
    fetchInitialSuggestions().then((items) => {
      setRecommendations(items.length > 0 ? items : INITIAL_SUGGESTIONS);
    });
  }, []);

  // Load a conversation by raw ID (used for ?c= URL param on mount)
  async function loadConversationById(id: string) {
    if (!user?.id) return;
    const detail: ConversationDetail | null = await getConversation(id, user.id);
    if (!detail) return;

    setConversationId(detail.id);
    setError(null);
    setInput("");

    const loaded: Message[] = detail.messages.map((m) => ({
      role: m.role as "user" | "assistant",
      content: m.content,
      sources: m.sources,
      // Reconstruct lightweight step chips from persisted sources — one
      // chip per tool, no live status. Gives loaded conversations the
      // same chip-strip look as fresh ones without persisting the full
      // step history on the backend.
      steps: m.role === "assistant" ? toStepsFromSources(m.sources) : [],
      bridges: [],
      traceId: m.trace_id,
      spanId: m.span_id,
    }));
    setMessages(loaded);

    const lastAiIdx = [...loaded].map((m, i) => ({ m, i })).reverse().find(({ m }) => m.role === "assistant");
    if (lastAiIdx) {
      setActiveMsgIdx(lastAiIdx.i);
    }

    if (detail.model && availableModels.includes(detail.model)) setSelectedModel(detail.model);
    if (detail.backend) setSelectedBackend(detail.backend as BackendType);

    setRecommendations(getFollowUpSuggestions(lastAiIdx?.m.sources ?? []));
  }

  // On mount: read ?c= from the URL and load that conversation
  useEffect(() => {
    const cId = new URLSearchParams(window.location.search).get('c');
    if (cId && user?.id) loadConversationById(cId);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps — intentionally runs once

  // Keep the URL ?c= param in sync with the active conversationId
  useEffect(() => {
    const url = new URL(window.location.href);
    if (conversationId) {
      url.searchParams.set('c', conversationId);
    } else {
      url.searchParams.delete('c');
    }
    window.history.replaceState(null, '', url.toString());
  }, [conversationId]);

  // Core submit logic — accepts query directly so pills can auto-submit without
  // relying on `input` state (avoids React batching issues).
  async function submit(query: string) {
    if (!query || loading) return;

    setInput("");
    setError(null);
    setLoading(true);

    const userMessage: Message = { role: "user", content: query, sources: [], steps: [], bridges: [] };
    // Empty assistant placeholder we mutate in place as stream events arrive.
    const placeholder: Message = { role: "assistant", content: "", sources: [], steps: [], bridges: [] };
    let assistantIdx = -1;
    setMessages((prev) => {
      const next = [...prev, userMessage, placeholder];
      assistantIdx = next.length - 1;
      setActiveMsgIdx(assistantIdx);
      return next;
    });
    trackQuerySubmitted(query.length);

    // Mutator helpers — wrap setMessages with the captured assistantIdx so
    // every event lands on the right slot even when React batches updates.
    const patchAssistant = (mutate: (m: Message) => Message) => {
      setMessages((prev) => prev.map((m, i) => (i === assistantIdx ? mutate(m) : m)));
    };
    const upsertStep = (step: StreamStep) => {
      patchAssistant((m) => {
        const existing = m.steps.findIndex((s) => s.id === step.id);
        return existing >= 0
          ? { ...m, steps: m.steps.map((s, i) => (i === existing ? { ...s, ...step } : s)) }
          : { ...m, steps: [...m.steps, step] };
      });
    };

    let finalSources: string[] = [];
    let finalAnswer = "";

    try {
      // Ensure a conversation exists before streaming the query
      let convId = conversationId;
      if (!convId && user?.id) {
        const shortTitle = query.length > 60 ? query.slice(0, 57) + "…" : query;
        const conv = await createConversation(user.id, shortTitle, selectedModel, selectedBackend);
        if (conv) {
          convId = conv.id;
          setConversationId(conv.id);
          setSidebarRefresh((n) => n + 1);
        }
      }

      for await (const evt of sendQueryStream(query, selectedModel, convId ?? undefined, user?.id ?? undefined)) {
        handleStreamEvent(evt);
      }

      function handleStreamEvent(evt: StreamEvent) {
        switch (evt.event) {
          case "step":
            upsertStep({
              id: `internal:${evt.step}`,
              kind: { kind: "internal", stepName: evt.step },
              status: evt.status === "running" ? "running" : evt.status === "done" ? "ok" : "error",
              resultSummary: evt.detail ?? undefined,
            });
            break;
          case "tool_call_start":
            upsertStep({
              id: evt.id,
              kind: { kind: "tool", toolName: evt.name },
              status: "running",
              argsJson: evt.args_json ?? null,
            });
            break;
          case "tool_call_end":
            upsertStep({
              id: evt.id,
              kind: { kind: "tool", toolName: evt.name },
              status: evt.status === "ok" ? "ok" : "error",
              resultSummary: evt.result_summary ?? undefined,
              durationMs: evt.duration_ms,
              sources: evt.sources,
            });
            break;
          case "text_chunk":
            finalAnswer += evt.chunk;
            patchAssistant((m) => ({ ...m, content: m.content + evt.chunk }));
            break;
          case "done":
            finalSources = evt.sources;
            patchAssistant((m) => ({
              ...m,
              sources: evt.sources,
              traceId: evt.trace_id,
              spanId: evt.span_id,
              bridges: extractBridgeData(m.content),
            }));
            if (evt.model && evt.model !== selectedModel) setSelectedModel(evt.model);
            break;
          case "error":
            setError({ message: evt.message, traceId: evt.trace_id });
            break;
        }
      }

      // Bridges extracted post-stream get tracked once for telemetry parity
      // with the non-streaming path.
      const bridges = extractBridgeData(finalAnswer);
      if (bridges.length > 0) trackBridgeCardRendered(bridges.length);

      // Static domain follow-ups first, then upgrade to LLM-generated ones.
      setRecommendations(getFollowUpSuggestions(finalSources));
      fetchSuggestions(query, finalAnswer, finalSources)
        .then((items) => { if (items.length > 0) setRecommendations(items); })
        .catch(() => { /* keep static fallback */ });
    } catch (err) {
      setError({
        message: err instanceof Error ? err.message : "Unknown error",
        traceId: err instanceof ApiError ? err.traceId : null,
      });
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

  async function handleSelectConversation(conv: ConversationSummary) {
    if (!user?.id) return;
    const detail: ConversationDetail | null = await getConversation(conv.id, user.id);
    if (!detail) return;

    setConversationId(conv.id);
    setSessionId(conv.id);
    setError(null);
    setInput("");

    const loaded: Message[] = detail.messages.map((m) => ({
      role: m.role as "user" | "assistant",
      content: m.content,
      sources: m.sources,
      steps: m.role === "assistant" ? toStepsFromSources(m.sources) : [],
      bridges: [],
      traceId: m.trace_id,
      spanId: m.span_id,
    }));
    setMessages(loaded);

    const lastAiIdx = [...loaded].map((m, i) => ({ m, i })).reverse().find(({ m }) => m.role === "assistant");
    if (lastAiIdx) {
      setActiveMsgIdx(lastAiIdx.i);
    }

    // Restore model/backend from conversation metadata
    if (detail.model && availableModels.includes(detail.model)) setSelectedModel(detail.model);
    if (detail.backend) setSelectedBackend(detail.backend as BackendType);

    setRecommendations(getFollowUpSuggestions(lastAiIdx?.m.sources ?? []));
  }

  const shortSha = VERSION && VERSION !== "local" ? VERSION : null;

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
          {user && (
            <IconButton
              data-testid="sidebar-toggle"
              aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}
              title={sidebarOpen ? "Close sidebar" : "Open sidebar"}
              size="xs"
              variant="ghost"
              colorPalette="gray"
              borderRadius="md"
              h="24px"
              w="24px"
              minW="24px"
              color="gray.400"
              _hover={{ color: "gray.600", bg: "gray.100" }}
              onClick={() => setSidebarOpen((v) => !v)}
            >
              <ChevronLeft size={14} style={{ transform: sidebarOpen ? "none" : "rotate(180deg)", transition: "transform 0.2s" }} />
            </IconButton>
          )}
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
          <Button
            data-testid="tab-chat"
            size="xs"
            variant={activeView === "chat" ? "solid" : "ghost"}
            colorPalette={activeView === "chat" ? "blue" : "gray"}
            borderRadius="md"
            fontWeight={activeView === "chat" ? "semibold" : "normal"}
            fontSize="xs"
            px={3}
            h="24px"
            onClick={() => setActiveView("chat")}
          >
            Chat
          </Button>
          <Button
            data-testid="tab-sandbox"
            data-tour="sandbox-tab"
            size="xs"
            variant={activeView === "sandbox" ? "solid" : "ghost"}
            colorPalette={activeView === "sandbox" ? "blue" : "gray"}
            borderRadius="md"
            fontWeight={activeView === "sandbox" ? "semibold" : "normal"}
            fontSize="xs"
            px={3}
            h="24px"
            onClick={() => setActiveView("sandbox")}
          >
            Sandbox
          </Button>
          {user?.is_admin && (
            <Button
              data-testid="tab-admin"
              size="xs"
              variant={activeView === "admin" ? "solid" : "ghost"}
              colorPalette={activeView === "admin" ? "purple" : "gray"}
              borderRadius="md"
              fontWeight={activeView === "admin" ? "semibold" : "normal"}
              fontSize="xs"
              px={3}
              h="24px"
              onClick={() => setActiveView("admin")}
            >
              Admin
            </Button>
          )}
        </HStack>

        <HStack gap={2}>
          <IconButton
            data-tour="tour-button"
            aria-label="Take product tour"
            title="Take product tour"
            size="xs"
            variant="ghost"
            colorPalette="gray"
            borderRadius="md"
            h="24px"
            w="24px"
            minW="24px"
            color="gray.400"
            _hover={{ color: "blue.600", bg: "blue.50" }}
            onClick={() => startTour()}
          >
            <Compass size={14} />
          </IconButton>
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
          {user && (
            <HStack gap={1.5}>
              <Text fontSize="xs" color="gray.400" display={{ base: "none", md: "block" }}>
                {user.email}
              </Text>
              <Button
                size="xs"
                variant="ghost"
                colorPalette="gray"
                borderRadius="md"
                fontSize="xs"
                h="24px"
                px={2}
                onClick={logout}
              >
                Sign out
              </Button>
            </HStack>
          )}
        </HStack>
      </Flex>

      {/* ── Body ───────────────────────────────────────────────────────────── */}
      {activeView === "sandbox" && <Sandbox />}
      {activeView === "admin" && <AdminTab />}
      <Flex flex={1} minH={0} overflow="hidden" display={activeView === "chat" ? "flex" : "none"}>
        {/* LEFT: conversation history sidebar */}
        {user && sidebarOpen && (
          <ConversationSidebar
            userId={user.id}
            activeId={conversationId}
            onSelect={handleSelectConversation}
            onNew={() => {
              setSessionId(newConversation());
              setMessages([]);
              setActiveMsgIdx(null);
              setRecommendations(INITIAL_SUGGESTIONS);
              setError(null);
              setInput("");
              setConversationId(null);
              setSidebarRefresh((n) => n + 1);
            }}
            refreshTrigger={sidebarRefresh}
          />
        )}
        {/* CENTER: chat column */}
        <Flex direction="column" flex={1} minW={0}>
          {/* Message thread */}
          <Box data-testid="message-thread" flex={1} overflowY="auto" px={5} py={6}>
            {messages.length === 0 ? (
              <EmptyState onSelect={submit} />
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
                        borderColor={msg.role === "assistant" && activeMsgIdx === i ? "blue.300" : "gray.200"}
                      >
                        {/* Tool / pipeline step chips render ABOVE the content
                            so users see the agent's reasoning as it runs.
                            Empty for user messages and for assistants that
                            never invoked a tool. */}
                        {msg.role === "assistant" && msg.steps.length > 0 && (
                          <VStack align="stretch" gap={1.5} mb={2}>
                            {msg.steps.map((step) => (
                              <ToolStepChip
                                key={step.id}
                                kind={step.kind}
                                status={step.status}
                                resultSummary={step.resultSummary}
                                durationMs={step.durationMs}
                                argsJson={step.argsJson}
                                sources={step.sources}
                                toolMeta={TOOL_META}
                              />
                            ))}
                          </VStack>
                        )}

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

                      </Box>

                      {msg.role === "assistant" && (
                        <MessageActions
                          content={msg.content}
                          traceId={msg.traceId}
                          spanId={msg.spanId}
                        />
                      )}
                    </Box>
                  </Flex>
                ))}

                {/* Global "Thinking..." indicator removed — the empty
                    assistant placeholder + first step chip serve the same
                    purpose live, and persistent loading state would render
                    a redundant empty bubble during streaming. */}

                {error && (
                  <Alert.Root status="error" borderRadius="xl" fontSize="sm">
                    <Alert.Indicator />
                    <Alert.Description>
                      {error.message}
                      {error.traceId && (
                        <Link
                          href={`https://${import.meta.env.VITE_DD_RUM_SITE || "us3.datadoghq.com"}/apm/trace/${error.traceId}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          ml={2}
                          fontWeight="medium"
                          textDecoration="underline"
                        >
                          View trace →
                        </Link>
                      )}
                    </Alert.Description>
                  </Alert.Root>
                )}

                <div ref={bottomRef} />
              </VStack>
            )}
          </Box>

          {/* Sources strip + CitationPanel removed — tool step chips on each
              assistant message now carry the same source / data_notes /
              source_url info inline via the expandable detail row. */}

          {/* ── Input bar ──────────────────────────────────────────────────── */}
          <Box
            data-testid="input-bar"
            borderTopWidth="1px"
            borderColor="gray.200"
            bg="white"
            px={5}
            pt={3}
            pb={4}
            flexShrink={0}
          >
            <VStack gap={2.5} align="stretch" maxW="3xl" mx="auto">
              <Box data-tour="recommendations">
                <QuerySuggestions suggestions={recommendations} onSelect={handleSuggestionSelect} disabled={loading} />
              </Box>
              <HStack gap={3} justify="flex-end" flexWrap="wrap">
                <HStack gap={1.5} align="center">
                  <Text fontSize="10px" color="gray.400" fontFamily="mono" letterSpacing="wide" textTransform="uppercase">Backend</Text>
                  <NativeSelect.Root
                    size="xs"
                    disabled={loading || !!conversationId}
                    title={conversationId ? "Backend is locked to the active conversation" : undefined}
                    data-dd-action-name="backend-select"
                  >
                    <NativeSelect.Field
                      id="backend-select"
                      aria-label="Select backend"
                      value={selectedBackend}
                      onChange={(e) => {
                        const b = e.target.value as BackendType;
                        setBackend(b);
                        setSelectedBackend(b);
                      }}
                      fontFamily="mono"
                      fontSize="xs"
                      opacity={conversationId ? 0.5 : 1}
                    >
                      <option value="python">Python</option>
                      <option value="dotnet">.NET</option>
                    </NativeSelect.Field>
                    <NativeSelect.Indicator />
                  </NativeSelect.Root>
                </HStack>
                <HStack gap={1.5} align="center">
                  <Text fontSize="10px" color="gray.400" fontFamily="mono" letterSpacing="wide" textTransform="uppercase">Model</Text>
                  <NativeSelect.Root size="xs" disabled={loading} data-dd-action-name="model-select">
                    <NativeSelect.Field
                      id="model-select"
                      aria-label="Select model"
                      value={selectedModel}
                      onChange={(e) => { setSelectedModel(e.target.value); setModel(e.target.value); }}
                      fontFamily="mono"
                      fontSize="xs"
                    >
                      {availableModels.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </NativeSelect.Field>
                    <NativeSelect.Indicator />
                  </NativeSelect.Root>
                </HStack>
              </HStack>
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
                  <SendHorizontal size={15} />
                </IconButton>
              </HStack>
              <Text fontSize="xs" color="gray.400" textAlign="center">
                Press <Text as="kbd" fontFamily="mono" px={1} bg="gray.100" borderRadius="sm">↵</Text> to send · <Text as="kbd" fontFamily="mono" px={1} bg="gray.100" borderRadius="sm">⇧↵</Text> for newline
              </Text>
            </VStack>
          </Box>
        </Flex>
      </Flex>
    </Flex>
  );
}
