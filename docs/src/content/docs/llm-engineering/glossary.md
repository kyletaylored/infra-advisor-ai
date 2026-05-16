---
title: Glossary
description: LLM Observability vocabulary, defined as concretely as possible. Reference page — not meant to be read top-to-bottom.
sidebar:
  order: 7
---

When DD docs (or this guide) say something like *"the eval-metric API joins by trace_id + span_id on the agent kind span"*, that sentence assumes you know what every noun means. This page is the lookup table. Definitions are kept concrete — what DD does with the thing, not the platonic ideal.

## ml_app

The grouping key for everything in LLM Observability. Traces, evals, prompts, datasets, dashboards — all keyed by `ml_app`. Set as a resource attribute on every span. We use one `ml_app` per service backend:

- `infra-advisor-ai` (Python)
- `infra-advisor-agent-api-dotnet` (.NET)

If `ml_app` is missing or wrong on a span, the span doesn't appear in LLMObs at all. Most "my traces aren't showing up" reports trace to this.

## Span kind

How DD classifies an LLMObs span into the agent decision tree. Standard kinds:

| Kind | Means |
|---|---|
| `workflow` | The top-level wrapper for a logical request |
| `agent` | A decision-making unit (router, planner, specialist) |
| `llm` | A raw LLM call (chat completion, embedding) |
| `tool` | A tool / function invocation |
| `task` | A deterministic step (parsing, lookup, formatting) |
| `embedding` | An embedding model call |
| `retrieval` | A vector store / knowledge base lookup |

DD auto-classifies some kinds from OTel GenAI semconv operation names (`chat → llm`, `invoke_agent → agent`, etc.). Custom kinds need the `dd.llmobs.span.kind` attribute set explicitly.

## Trace, span, parent span

Standard distributed-tracing vocabulary, but DD-specific behavior worth noting:

- **Trace** — all spans sharing a `trace_id`. One logical request.
- **Span** — one operation, identified by `(trace_id, span_id)`. Has a `parent_span_id` (root spans have null parent).
- **Parent span** — the span this one is nested under. DD's UI renders this as a tree, so picking a sensible parent matters for readability.

W3C `traceparent` header propagates trace context across services. As long as both ends use the same SDK (or two SDKs that respect W3C), the trace stays unified across hops.

## Join — join_on.span vs join_on.tag

When attaching an evaluation to a span via the eval-metric API, you choose how to address the target span:

- **`join_on.span`** — by `(trace_id, span_id)`. Exact target, requires you captured those IDs when emitting the span.
- **`join_on.tag`** — by an arbitrary tag like `session.id`. Matches every span with that tag value within the configurable window.

The `.span` form is what our `IResponseEvaluator` pipeline uses; the `.tag` form is useful for retroactive scoring (e.g., "score every span from session X").

## Source attribute

A resource attribute that tells DD how the span was emitted:

- `source = otel` — the OTel SDK emitted it. DD's OTLP ingest path classifies these into LLMObs kinds.
- `source = ddtrace` (implicit) — the DD tracer emitted it.
- `source = undefined` — something is misconfigured.

Why it matters: the external-evaluations API tags evals with `source:otel` to match against OTel-emitted spans. If the span side and eval side disagree, the join fails silently — no error, just no score visible in the UI.

## Auto-instrumentation vs explicit spans

- **Auto-instrumentation** — the SDK patches a library's call sites and emits spans without code changes. ddtrace covers LangChain, OpenAI, MCP, etc. OTel libraries opt in via `.UseOpenTelemetry()` decorators.
- **Explicit span** — code wraps a region in a context manager (`with LLMObs.workflow():`) or an Activity (`using var a = source.StartActivity()`) so it becomes a visible step.

Both produce spans of the same shape. Use auto where it exists; explicit for orchestration steps the libraries don't cover.

## Managed eval, LLM-judge, external eval

The three flavors of evaluator in DD:

- **Managed eval** — DD-built, runs in DD's pipeline. UI toggle. Example: Language Mismatch.
- **LLM-judge (DD UI)** — Custom prompt + model, configured in DD UI. Runs in DD's pipeline. Example: "rate helpfulness 1-5".
- **External eval** — Code-driven, runs in your app, POSTs scores to DD's eval-metric API. Example: `CitationPresentEvaluator`.

All three end up on the span in the same shape (`@evaluations.<label>.value`). Choose by where the logic should live.

## Dataset, experiment run

Offline regression-testing primitives (see [Experiments](./experiments/)):

- **Dataset** — frozen set of `(input, expected)` rows. Curated from production traces.
- **Experiment run** — one pass over a dataset using a specific app version + prompt. Each row gets an LLM call + evaluator scores.

## Prompt tracking

Versioning system prompts by content hash so DD can group spans by prompt version. Stored on each LLM span as a JSON blob under the `_dd.ml_obs.prompt_tracking` attribute. See [Monitoring → Prompt tracking](./monitoring/prompt-tracking/).

## Session

A series of LLMObs traces sharing a `session.id` tag. Typically one browser tab or one phone session. We set `session.id` from the RUM session header so LLMObs sessions click through to RUM session replay.

Distinct from `gen_ai.conversation.id`, which is one chat thread (potentially across sessions). Both can coexist.

## Annotation queue

DD UI feature for humans to score traces. Pulls from a filter you define, presents the trace to a reviewer, captures answers to a schema. Output: evals on the span, just like programmatic evaluators. See [Evaluations → Annotation queues](./evaluations/annotation-queues/).

## Faithfulness, groundedness, relevance

The three most-cited "is the answer good?" evaluator concepts. Slightly different things:

- **Relevance** — does the answer address the question?
- **Groundedness** — is the answer supported by the retrieved/tool context?
- **Faithfulness** — does the answer avoid hallucinating beyond the provided context?

In our app, MAF's `RelevanceEvaluator` covers Relevance, `GroundednessEvaluator` covers Groundedness, and Python's faithfulness eval is a custom variant of Groundedness with our specific context shape.

## DD eval-metric API

`POST /api/intake/llm-obs/v2/eval-metric`. The wire format for submitting external evaluations. See [Evaluations → External](./evaluations/external/#the-wire-format).

## What's next

The rest of the guide. If a term came up that isn't here, [open an issue](https://github.com/kyletaylored/infra-advisor-ai/issues) and we'll add it.
