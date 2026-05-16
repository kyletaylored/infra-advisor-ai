---
title: Recipes
description: Short how-tos for the most common day-to-day instrumentation tasks.
sidebar:
  order: 6
  label: Recipes
---

Each recipe assumes you've read the [Overview](../) at least once. Open the relevant feature page for full context — these are the muscle-memory steps.

## Add a new evaluator

1. Implement `IResponseEvaluator` in `Services/Evaluators/` (see [Evaluations](../evaluations/#extending-it--adding-a-deterministic-evaluator)).
2. `builder.Services.AddSingleton<IResponseEvaluator, MyEvaluator>()` in `Program.cs`.
3. Done — DI picks it up automatically.

## Add a new business counter

See [Metrics → Extending it](../metrics/#extending-it--adding-a-new-counter).

## Add OTel tracing to a new .NET service

1. Add NuGet packages: `OpenTelemetry.Extensions.Hosting`, `OpenTelemetry.Instrumentation.AspNetCore`, `OpenTelemetry.Instrumentation.Http`, `OpenTelemetry.Exporter.OpenTelemetryProtocol`.
2. Copy the `TelemetrySetup.cs` pattern from `agent-api-dotnet` — change `ActivitySourceName` + `serviceName`.
3. Configure env vars: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`, `DD_ENV`, `DD_VERSION`.
4. In `Program.cs`: `TelemetrySetup.Configure(builder)` before `builder.Build()`.

## A/B test a system prompt

Define a second prompt constant, hash it for the version tag, pick one per request (random or sticky per user-ID hash), stamp the chosen version's JSON on the activity. The Prompt Tracking foundation in [Span kinds & prompts](../span-kinds/#prompt-tracking) handles the rest.

## Look at all spans for one conversation

DD trace explorer: `@ml_app:infra-advisor-agent-api-dotnet @meta.gen_ai.conversation.id:<id>`. Group by `trace_id`.

## Debug why an evaluator isn't running

1. Check `EVAL_SAMPLE_RATE` env — must be > 0.
2. Confirm `DD_API_KEY` secret mounted (`DatadogEvalsClient` disables silently when missing — logs a WARN at startup).
3. Pod logs for `"DD eval submission failed"` warnings.
4. Confirm `AgentSpanContext.Current` populated — the ActivityListener in `Program.cs` should fire on every `invoke_agent` span.
5. Easiest path: open the admin UI's **Eval pipeline (read-only)** panel. The "Recent submissions" table shows exactly what fired, whether it succeeded, and why if it failed.
