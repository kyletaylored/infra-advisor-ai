---
title: Span kinds & prompt tracking
description: How the seven LLMObs span kinds get emitted, and how the system prompt is versioned for per-version dashboards.
sidebar:
  order: 2
  label: Span kinds & prompts
---

Each feature follows the same shape:
- **Goal** — the problem it solves
- **How we wired it** — code pointers
- **Outcome** — what you see in DD
- **Extending it** — recipe for adding more

## Span kinds (workflow / agent / llm / tool / task / embedding / retrieval)

**Goal:** show the full agent decision tree, including non-LLM steps (classification, retrieval). Anything you'd want to graph latency / cost / failure rate by should be its own span kind.

| Kind | Source | Code |
|---|---|---|
| `workflow` | AspNetCore auto-instrumentation | `TelemetrySetup.cs: AddAspNetCoreInstrumentation()` |
| `agent` | MAF `.UseOpenTelemetry()` on agent builder | `Program.cs` AIAgent registration |
| `llm` | M.E.AI `.UseOpenTelemetry()` on chat client | `Program.cs` IChatClient registration |
| `tool` | M.E.AI `.UseFunctionInvocation()` | same line as `llm` |
| `task` | Manual `ActivitySource.StartActivity` + `dd.llmobs.span.kind=task` | `AgentService.ClassifyDomainTraced` |
| `embedding` | M.E.AI `.UseOpenTelemetry()` on IEmbeddingGenerator | `Program.cs` IEmbeddingGenerator registration |
| `retrieval` | Manual span + `dd.llmobs.span.kind=retrieval` | `RetrievalService.RetrieveAsync` |

DD auto-maps `chat → llm`, `invoke_agent → agent`, `execute_tool → tool`, `embeddings → embedding`. `task` and `retrieval` aren't in the OTel semconv as gen_ai operation names — we explicitly tag `dd.llmobs.span.kind=...` to force the classification.

**Extending it** — adding a new kind:

```csharp
// 1. Start from the same ActivitySource TelemetrySetup AddSource's
using var activity = ActivitySource.StartActivity("my_step", ActivityKind.Internal);

// 2. Tag the kind + a standard operation name for context
activity?.SetTag("dd.llmobs.span.kind", "task");      // or retrieval, etc.
activity?.SetTag("gen_ai.operation.name", "my_step");

// 3. Add input/output for the LLMObs UI to render
activity?.SetTag("input.value", queryOrInput);
// ... do the work ...
activity?.SetTag("output.value", result);
```

## Prompt tracking

**Goal:** version the system prompt so changes are traceable in production. When prompt v2 ships, you want to compare latency / cost / quality eval scores between v1 and v2 by version tag without rebuilding dashboards.

**How we wired it:** `Program.cs` computes a content-derived version (`v1-<sha256[:8]>`) and registers a global `ActivityListener` that stamps `_dd.ml_obs.prompt_tracking` JSON metadata on every `invoke_agent` and `chat` span:

```csharp
var promptVersion = "v1-" + ShortContentHash(AgentSystemPrompt);
var promptTrackingJson = JsonSerializer.Serialize(new {
    name = "infra-advisor-system",
    version = promptVersion,
    template = AgentSystemPrompt,
    variables = new Dictionary<string, object>(),
});

ActivitySource.AddActivityListener(new ActivityListener { /* stamps spans */ });
```

**Outcome:** DD UI → LLM Observability → Prompts shows `infra-advisor-system v1-<hash>` with version diff capability. Once a v2 prompt ships, comparison charts (calls, latency, tokens, cost per version) work without further code.

## References

- [DD Prompt Tracking](https://docs.datadoghq.com/llm_observability/monitoring/prompt_tracking/)
- [OTel GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)
