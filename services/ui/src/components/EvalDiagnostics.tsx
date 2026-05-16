import { useEffect, useState } from "react";
import { Badge, Box, Flex, HStack, Spinner, Table, Text, VStack } from "@chakra-ui/react";
import { Check, X, Bot, Filter as FilterIcon, BookOpen, Layers } from "lucide-react";

// ── Types matching the /eval/status response from agent-api-dotnet ─────────────

interface EvaluatorInfo {
  label: string;
  type_name: string;
  is_llm_judge: boolean;
}

interface SubmissionEntry {
  timestamp_iso: string;
  label: string;
  metric_type: "boolean" | "score" | "categorical";
  value: boolean | number | string | null;
  success: boolean;
  duration_ms: number;
  trace_id_decimal: string | null;
  span_id_decimal: string | null;
  reasoning: string | null;
  error: string | null;
}

interface EvalStatus {
  sample_rate: number;
  eval_pipeline: { registered_evaluators: EvaluatorInfo[] };
  datadog: { enabled: boolean; ml_app: string; site: string; api_key_configured: boolean };
  judge: { deployment: string; note: string };
  submissions: {
    total: number;
    failed: number;
    success_rate: number | null;
    recent: SubmissionEntry[];
  };
}

// ── Component ──────────────────────────────────────────────────────────────────

// Polling cadence: cheap endpoint + 50-entry ring buffer means refresh
// every 10s is fine. Admins are expected to look briefly, not stare.
const POLL_INTERVAL_MS = 10_000;

// Currently the /eval/status endpoint is only exposed on the .NET backend.
// Hardcoding the path matches what the rest of the admin / chat code does
// for backend-specific endpoints (see api.ts getApiBase). We bypass the
// helper because admin diagnostics should always show the .NET backend's
// pipeline regardless of which backend the user has selected for /query.
const STATUS_URL = "/api-dotnet/eval/status";

export function EvalDiagnostics() {
  const [status, setStatus] = useState<EvalStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const resp = await fetch(STATUS_URL, { credentials: "include" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data: EvalStatus = await resp.json();
        if (!cancelled) {
          setStatus(data);
          setError(null);
          setLastUpdated(new Date());
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unknown error");
      }
    }
    poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (error && !status) {
    return (
      <Box borderWidth="1px" borderRadius="lg" p={4} bg="red.50" borderColor="red.200">
        <Text fontSize="sm" color="red.700">Failed to load /eval/status: {error}</Text>
      </Box>
    );
  }

  if (!status) {
    return (
      <Box borderWidth="1px" borderRadius="lg" p={4}>
        <HStack gap={2}>
          <Spinner size="xs" />
          <Text fontSize="sm" color="gray.600">Loading eval pipeline diagnostics...</Text>
        </HStack>
      </Box>
    );
  }

  return (
    <VStack align="stretch" gap={4}>
      {/* ── Header row: pipeline state at a glance ──────────────────── */}
      <Box borderWidth="1px" borderRadius="lg" p={4} bg="white">
        <Flex justify="space-between" align="center" mb={3}>
          <HStack gap={2}>
            <Layers size={16} color="#3b82f6" />
            <Text fontWeight="semibold" fontSize="sm">Eval pipeline</Text>
          </HStack>
          {lastUpdated && (
            <Text fontSize="xs" color="gray.500">
              Updated {lastUpdated.toLocaleTimeString()}
            </Text>
          )}
        </Flex>

        <Flex wrap="wrap" gap={6}>
          <Stat
            label="Sample rate"
            value={`${(status.sample_rate * 100).toFixed(0)}%`}
            note={`EVAL_SAMPLE_RATE env. Pod restart required to change.`}
          />
          <Stat
            label="DD submission"
            value={status.datadog.enabled ? "enabled" : "disabled"}
            tone={status.datadog.enabled ? "green" : "red"}
            note={status.datadog.enabled
              ? `ml_app: ${status.datadog.ml_app} · site: ${status.datadog.site}`
              : "DD_API_KEY not set"}
          />
          <Stat
            label="Judge model"
            value={status.judge.deployment}
            note="LLM-as-judge inference deployment"
          />
          <Stat
            label="Total submitted"
            value={status.submissions.total.toLocaleString()}
            note={`${status.submissions.failed} failed${status.submissions.success_rate !== null ? ` · ${(status.submissions.success_rate * 100).toFixed(1)}% success` : ""}`}
          />
        </Flex>

        <Text fontSize="2xs" color="gray.500" mt={3}>{status.judge.note}</Text>
      </Box>

      {/* ── Registered evaluators ───────────────────────────────────── */}
      <Box borderWidth="1px" borderRadius="lg" p={4} bg="white">
        <HStack gap={2} mb={3}>
          <FilterIcon size={16} color="#3b82f6" />
          <Text fontWeight="semibold" fontSize="sm">Registered evaluators</Text>
          <Badge variant="subtle" colorPalette="gray" fontSize="2xs">
            {status.eval_pipeline.registered_evaluators.length}
          </Badge>
        </HStack>
        <VStack align="stretch" gap={1.5}>
          {status.eval_pipeline.registered_evaluators.map((ev) => (
            <Flex
              key={ev.label}
              borderWidth="1px"
              borderColor="gray.200"
              borderRadius="md"
              px={3}
              py={2}
              align="center"
              gap={3}
              bg="gray.50"
            >
              {ev.is_llm_judge ? <Bot size={14} color="#8b5cf6" /> : <BookOpen size={14} color="#6b7280" />}
              <Text fontSize="sm" fontWeight="medium">{ev.label}</Text>
              <Text fontSize="xs" color="gray.500">{ev.type_name}</Text>
              <Badge ml="auto" variant="subtle"
                colorPalette={ev.is_llm_judge ? "purple" : "gray"}
                fontSize="2xs">
                {ev.is_llm_judge ? "LLM judge" : "deterministic"}
              </Badge>
            </Flex>
          ))}
        </VStack>
      </Box>

      {/* ── Recent submissions table ────────────────────────────────── */}
      <Box borderWidth="1px" borderRadius="lg" p={4} bg="white">
        <Flex justify="space-between" align="center" mb={3}>
          <HStack gap={2}>
            <Layers size={16} color="#3b82f6" />
            <Text fontWeight="semibold" fontSize="sm">Recent submissions</Text>
            <Badge variant="subtle" colorPalette="gray" fontSize="2xs">
              {status.submissions.recent.length}
            </Badge>
          </HStack>
          <Text fontSize="2xs" color="gray.400">Newest first · capped at 50</Text>
        </Flex>

        {status.submissions.recent.length === 0 ? (
          <Text fontSize="sm" color="gray.500">
            No submissions yet. Fire a /query — at 10% sample rate, ~1 in 10 traces will appear here.
          </Text>
        ) : (
          <Box overflowX="auto">
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row>
                  <Table.ColumnHeader>Time</Table.ColumnHeader>
                  <Table.ColumnHeader>Label</Table.ColumnHeader>
                  <Table.ColumnHeader>Value</Table.ColumnHeader>
                  <Table.ColumnHeader>Status</Table.ColumnHeader>
                  <Table.ColumnHeader>Duration</Table.ColumnHeader>
                  <Table.ColumnHeader>Reasoning / error</Table.ColumnHeader>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {status.submissions.recent.map((s, i) => (
                  <Table.Row key={i}>
                    <Table.Cell fontSize="2xs" color="gray.500" whiteSpace="nowrap">
                      {new Date(s.timestamp_iso).toLocaleTimeString()}
                    </Table.Cell>
                    <Table.Cell fontSize="xs" fontWeight="medium">{s.label}</Table.Cell>
                    <Table.Cell fontSize="xs">
                      <FormatValue mt={s.metric_type} v={s.value} />
                    </Table.Cell>
                    <Table.Cell>
                      {s.success
                        ? <HStack gap={1}><Check size={12} color="#16a34a" /><Text fontSize="2xs" color="green.700">ok</Text></HStack>
                        : <HStack gap={1}><X size={12} color="#dc2626" /><Text fontSize="2xs" color="red.700">fail</Text></HStack>}
                    </Table.Cell>
                    <Table.Cell fontSize="2xs" color="gray.500">{s.duration_ms}ms</Table.Cell>
                    <Table.Cell fontSize="2xs" color="gray.600" maxW="320px">
                      <Text lineClamp={2}>{s.error ?? s.reasoning ?? "—"}</Text>
                    </Table.Cell>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          </Box>
        )}
      </Box>
    </VStack>
  );
}

// ── Tiny helpers ──────────────────────────────────────────────────────────────

function Stat({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "green" | "red" | "gray" }) {
  return (
    <Box minW="180px">
      <Text fontSize="2xs" color="gray.500" textTransform="uppercase" letterSpacing="wider">{label}</Text>
      <Text
        fontSize="lg"
        fontWeight="semibold"
        color={tone === "red" ? "red.600" : tone === "green" ? "green.700" : "gray.800"}
      >
        {value}
      </Text>
      {note && <Text fontSize="2xs" color="gray.500" mt={0.5}>{note}</Text>}
    </Box>
  );
}

function FormatValue({ mt, v }: { mt: string; v: boolean | number | string | null }) {
  if (v === null || v === undefined) return <Text color="gray.400">—</Text>;
  if (mt === "boolean") {
    return v
      ? <Badge variant="subtle" colorPalette="green" fontSize="2xs">true</Badge>
      : <Badge variant="subtle" colorPalette="red" fontSize="2xs">false</Badge>;
  }
  if (mt === "score") return <Text fontFamily="mono">{Number(v).toFixed(2)}</Text>;
  return <Text>{String(v)}</Text>;
}
