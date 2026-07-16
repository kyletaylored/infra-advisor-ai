import { useEffect, useState } from "react";
import { Badge, Box, Flex, HStack, Spinner, Table, Text, VStack } from "@chakra-ui/react";
import { Check, X, ShieldAlert } from "lucide-react";

// ── Types matching the /ai-guard/status response from agent-api-dotnet ─────────

interface AiGuardEntry {
  timestamp_iso: string;
  action: "ALLOW" | "DENY" | "ABORT";
  reason: string | null;
  success: boolean;
  duration_ms: number;
  trace_id_decimal: string | null;
  span_id_decimal: string | null;
  error: string | null;
}

interface AiGuardStatus {
  datadog: { enabled: boolean; note: string };
  evaluations: {
    total: number;
    blocked: number;
    failed: number;
    recent: AiGuardEntry[];
  };
}

// ── Component ──────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 10_000;

// AI Guard's HTTP API path (the only option for the .NET/MAF backend) sends
// no traces to Datadog, so this panel — like /eval/status — is .NET-only and
// hardcoded regardless of which backend the user has selected for /query.
const STATUS_URL = "/api-dotnet/ai-guard/status";

export function AiGuardDiagnostics() {
  const [status, setStatus] = useState<AiGuardStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const resp = await fetch(STATUS_URL, { credentials: "include" });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data: AiGuardStatus = await resp.json();
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
        <Text fontSize="sm" color="red.700">Failed to load /ai-guard/status: {error}</Text>
      </Box>
    );
  }

  if (!status) {
    return (
      <Box borderWidth="1px" borderRadius="lg" p={4}>
        <HStack gap={2}>
          <Spinner size="xs" />
          <Text fontSize="sm" color="gray.600">Loading AI Guard diagnostics...</Text>
        </HStack>
      </Box>
    );
  }

  return (
    <VStack align="stretch" gap={4}>
      <Box borderWidth="1px" borderRadius="lg" p={4} bg="white">
        <Flex justify="space-between" align="center" mb={3}>
          <HStack gap={2}>
            <ShieldAlert size={16} color="#3b82f6" />
            <Text fontWeight="semibold" fontSize="sm">AI Guard (HTTP API)</Text>
          </HStack>
          {lastUpdated && (
            <Text fontSize="xs" color="gray.500">
              Updated {lastUpdated.toLocaleTimeString()}
            </Text>
          )}
        </Flex>

        <Flex wrap="wrap" gap={6}>
          <Stat
            label="AI Guard"
            value={status.datadog.enabled ? "enabled" : "disabled"}
            tone={status.datadog.enabled ? "green" : "red"}
            note={status.datadog.enabled
              ? "Pre-flight check on every /query and /query/stream request"
              : "DD_API_KEY/DD_APPLICATION_KEY not set — fails open (ALLOW)"}
          />
          <Stat
            label="Total evaluated"
            value={status.evaluations.total.toLocaleString()}
            note={`${status.evaluations.failed} failed calls`}
          />
          <Stat
            label="Blocked"
            value={status.evaluations.blocked.toLocaleString()}
            tone={status.evaluations.blocked > 0 ? "red" : "gray"}
            note="DENY or ABORT actions"
          />
        </Flex>

        <Text fontSize="2xs" color="gray.500" mt={3}>{status.datadog.note}</Text>
      </Box>

      <Box borderWidth="1px" borderRadius="lg" p={4} bg="white">
        <Flex justify="space-between" align="center" mb={3}>
          <HStack gap={2}>
            <ShieldAlert size={16} color="#3b82f6" />
            <Text fontWeight="semibold" fontSize="sm">Recent evaluations</Text>
            <Badge variant="subtle" colorPalette="gray" fontSize="2xs">
              {status.evaluations.recent.length}
            </Badge>
          </HStack>
          <Text fontSize="2xs" color="gray.400">Newest first · capped at 50</Text>
        </Flex>

        {status.evaluations.recent.length === 0 ? (
          <Text fontSize="sm" color="gray.500">
            No evaluations yet. Every /query and /query/stream request triggers one.
          </Text>
        ) : (
          <Box overflowX="auto">
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row>
                  <Table.ColumnHeader>Time</Table.ColumnHeader>
                  <Table.ColumnHeader>Action</Table.ColumnHeader>
                  <Table.ColumnHeader>HTTP call</Table.ColumnHeader>
                  <Table.ColumnHeader>Duration</Table.ColumnHeader>
                  <Table.ColumnHeader>Reason / error</Table.ColumnHeader>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {status.evaluations.recent.map((e, i) => (
                  <Table.Row key={i}>
                    <Table.Cell fontSize="2xs" color="gray.500" whiteSpace="nowrap">
                      {new Date(e.timestamp_iso).toLocaleTimeString()}
                    </Table.Cell>
                    <Table.Cell>
                      <Badge
                        variant="subtle"
                        colorPalette={e.action === "ALLOW" ? "green" : "red"}
                        fontSize="2xs"
                      >
                        {e.action}
                      </Badge>
                    </Table.Cell>
                    <Table.Cell>
                      {e.success
                        ? <HStack gap={1}><Check size={12} color="#16a34a" /><Text fontSize="2xs" color="green.700">ok</Text></HStack>
                        : <HStack gap={1}><X size={12} color="#dc2626" /><Text fontSize="2xs" color="red.700">fail</Text></HStack>}
                    </Table.Cell>
                    <Table.Cell fontSize="2xs" color="gray.500">{e.duration_ms}ms</Table.Cell>
                    <Table.Cell fontSize="2xs" color="gray.600" maxW="320px">
                      <Text lineClamp={2}>{e.error ?? e.reason ?? "—"}</Text>
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
