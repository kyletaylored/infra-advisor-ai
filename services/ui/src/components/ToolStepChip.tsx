import { useState } from "react";
import { Box, Collapsible, HStack, Icon, IconButton, Spinner, Text, VStack } from "@chakra-ui/react";
import { Check, ChevronDown, ChevronRight, Search, AlertCircle, BookOpen, Filter, FlaskConical } from "lucide-react";

// Maps tool name → display label + glyph. The label here is the short user-
// facing name shown in the chip; the optional `data_notes` is rendered in
// the expanded detail row to give users the same context the (now-removed)
// CitationPanel used to show.
export interface ToolDisplayMeta {
  label: string;
  data_notes?: string;
  source_url?: string;
}

export type StepKind =
  | { kind: "tool"; toolName: string }      // model tool call (e.g. get_contract_awards)
  | { kind: "internal"; stepName: string }; // pipeline step (classify_domain, retrieve_best_practices)

export type StepStatus = "running" | "ok" | "error";

export interface ToolStepChipProps {
  kind: StepKind;
  status: StepStatus;
  // Filled in once the step completes — short one-liner shown in the chip.
  resultSummary?: string | null;
  // milliseconds — rendered only when > 0
  durationMs?: number;
  // Arguments the model sent, JSON-stringified. Rendered in expanded detail
  // when present.
  argsJson?: string | null;
  // Per-step source citations. For internal steps this is empty.
  sources?: string[];
  // Tool name → display metadata; supplied by Chat.tsx so this component
  // doesn't need its own copy of TOOL_META.
  toolMeta: Record<string, ToolDisplayMeta>;
  // Opens the Sandbox tab pre-populated with this tool + its args, for
  // re-testing a failed/interesting call. Only rendered for tool-kind steps
  // (internal pipeline steps have no tool to open).
  onOpenInSandbox?: () => void;
}

const INTERNAL_LABELS: Record<string, string> = {
  classify_domain: "Classify intent",
  retrieve_best_practices: "Best-practice retrieval",
};

export function ToolStepChip(props: ToolStepChipProps) {
  const [expanded, setExpanded] = useState(false);

  const label = props.kind.kind === "tool"
    ? (props.toolMeta[props.kind.toolName]?.label ?? props.kind.toolName)
    : (INTERNAL_LABELS[props.kind.stepName] ?? props.kind.stepName);

  const dataNotes = props.kind.kind === "tool"
    ? props.toolMeta[props.kind.toolName]?.data_notes
    : undefined;

  const sourceUrl = props.kind.kind === "tool"
    ? props.toolMeta[props.kind.toolName]?.source_url
    : undefined;

  const hasDetail =
    !!props.argsJson ||
    !!dataNotes ||
    !!sourceUrl ||
    (props.sources && props.sources.length > 0);

  const Glyph = props.kind.kind === "internal"
    ? (props.kind.stepName === "classify_domain" ? Filter : BookOpen)
    : Search;

  const tone = props.status === "error" ? "red" : "gray";

  return (
    <Box
      data-testid={`step-chip-${props.kind.kind === "tool" ? props.kind.toolName : props.kind.stepName}`}
      borderWidth="1px"
      borderColor={`${tone}.200`}
      bg={`${tone}.50`}
      borderRadius="md"
      px={2.5}
      py={1.5}
      fontSize="xs"
    >
      <HStack
        gap={2}
        align="center"
        onClick={() => hasDetail && setExpanded((e) => !e)}
        cursor={hasDetail ? "pointer" : "default"}
        role={hasDetail ? "button" : undefined}
        aria-expanded={hasDetail ? expanded : undefined}
      >
        {/* leading state icon */}
        {props.status === "running" ? (
          <Spinner size="xs" color="blue.500" />
        ) : props.status === "error" ? (
          <Icon as={AlertCircle} boxSize={3.5} color="red.600" />
        ) : (
          <Icon as={Check} boxSize={3.5} color="green.600" />
        )}

        <Icon as={Glyph} boxSize={3.5} color={`${tone}.500`} />

        <Text fontWeight="medium" color="gray.800">{label}</Text>

        {props.resultSummary && (
          <Text color="gray.500">· {props.resultSummary}</Text>
        )}

        {props.durationMs !== undefined && props.durationMs > 0 && (
          <Text color="gray.400">· {formatDuration(props.durationMs)}</Text>
        )}

        {(props.kind.kind === "tool" && props.onOpenInSandbox) || hasDetail ? (
          <HStack gap={1} ml="auto">
            {props.kind.kind === "tool" && props.onOpenInSandbox && (
              <IconButton
                aria-label="Open in Sandbox"
                title="Open in Sandbox with these inputs"
                size="2xs"
                variant="ghost"
                colorPalette="gray"
                color="gray.400"
                onClick={(e) => {
                  e.stopPropagation();
                  props.onOpenInSandbox?.();
                }}
              >
                <FlaskConical size={12} />
              </IconButton>
            )}
            {hasDetail && (
              <Icon as={expanded ? ChevronDown : ChevronRight} boxSize={3.5} color="gray.400" />
            )}
          </HStack>
        ) : null}
      </HStack>

      <Collapsible.Root open={expanded}>
        <Collapsible.Content>
          <VStack align="stretch" gap={1.5} mt={2} pt={2} borderTopWidth="1px" borderColor={`${tone}.200`}>
            {props.argsJson && (
              <Box>
                <Text fontWeight="semibold" color="gray.600" fontSize="2xs">Arguments</Text>
                <Box
                  as="pre"
                  fontSize="2xs"
                  fontFamily="mono"
                  whiteSpace="pre-wrap"
                  color="gray.700"
                  bg="white"
                  borderRadius="sm"
                  p={1.5}
                  borderWidth="1px"
                  borderColor="gray.200"
                  maxH="120px"
                  overflowY="auto"
                >
                  {prettyJson(props.argsJson)}
                </Box>
              </Box>
            )}
            {dataNotes && (
              <Box>
                <Text fontWeight="semibold" color="gray.600" fontSize="2xs">About this data</Text>
                <Text color="gray.700" fontSize="2xs">{dataNotes}</Text>
              </Box>
            )}
            {props.sources && props.sources.length > 0 && (
              <Box>
                <Text fontWeight="semibold" color="gray.600" fontSize="2xs">Citations</Text>
                <Text color="gray.700" fontSize="2xs">{props.sources.join(", ")}</Text>
              </Box>
            )}
            {sourceUrl && (
              <Box>
                <Text fontWeight="semibold" color="gray.600" fontSize="2xs">Data source</Text>
                <Box
                  as="a"
                  // @ts-expect-error Chakra Box doesn't type anchor props
                  href={sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  color="blue.600"
                  fontSize="2xs"
                  textDecoration="underline"
                >
                  {sourceUrl}
                </Box>
              </Box>
            )}
          </VStack>
        </Collapsible.Content>
      </Collapsible.Root>
    </Box>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
