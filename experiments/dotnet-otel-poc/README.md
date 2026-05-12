# .NET OTel GenAI POC

Minimal ASP.NET Core + Microsoft.Extensions.AI app that validates the path from
**.NET application code → OTLP → Datadog LLM Observability** in isolation from
the main service. Throwaway — lives under `experiments/` and is not wired into
CI/CD or Kubernetes.

## What it demonstrates

A single `POST /chat` call exercises every LLMObs span kind:

```
invoke_agent                   (kind: agent      | manual ActivitySource span)
└── router                     (kind: workflow   | manual ActivitySource span)
└── specialist-{name}          (kind: workflow   | manual ActivitySource span)
    └── prepare-context        (kind: task       | manual ActivitySource span)
    └── chat <model>           (kind: llm        | Microsoft.Extensions.AI)
        └── execute_tool ...   (kind: tool       | Microsoft.Extensions.AI / UseFunctionInvocation)
```

Key design choices, all in `Program.cs`:

- **`Microsoft.Extensions.AI`** wraps the LLM call with `.UseOpenTelemetry()` so
  `chat` spans get correct OTel GenAI semantic-convention attributes for free —
  no hand-rolled `gen_ai.*` tagging code.
- **`.UseFunctionInvocation()`** runs the tool loop and emits an `execute_tool`
  span per call, so the tool kind drops out of the framework for free too.
- **One shared `ActivitySource`** (`infra-advisor-otel-poc`) is passed to both
  the M.E.AI decorator and the manual agent/workflow/task spans, so a single
  `AddSource(...)` call covers the whole pipeline.
- **`invoke_agent` is a true root span** (fresh `TraceId`, zero parent `SpanId`)
  — this is the fix for the orphaned-parent issue from the main service where
  DD LLMObs silently dropped UI-originated traces because their parent (the
  ASP.NET HTTP span) wasn't in the OTLP batch. The APM trace ID is kept on the
  span as `apm.trace_id` for cross-product correlation.
- **Browser RUM** in `wwwroot/index.html` correlates UI sessions to LLMObs via
  the `sessionId` → `gen_ai.conversation.id` tag.

## Running locally

```bash
cd experiments/dotnet-otel-poc
cp .env.example .env
# edit .env with your Azure OpenAI + DD_API_KEY values
set -a; source .env; set +a
dotnet run
```

Open <http://localhost:5000> in a browser, type a query, hit Send.

## Verifying in Datadog (US3)

1. **Trace export firing** — pod stdout / `dotnet run` output shows OTLP HTTP
   POST to `https://otlp.us3.datadoghq.com/v1/traces` with `202 Accepted`:
   ```
   info: System.Net.Http.HttpClient.OtlpTraceExporter.LogicalHandler[101]
         End processing HTTP request after 137ms - 202
   ```
2. **LLMObs UI** — go to LLM Observability → Traces, filter by
   `ml_app:infra-advisor-otel-poc`. Each `/chat` call should produce one trace
   with the full hierarchy above. The displayed `session_id` matches the
   `gen_ai.conversation.id` shown in the browser footer.
3. **APM ↔ LLMObs correlation** — the `apm.trace_id` tag on `invoke_agent` links
   back to the HTTP request's APM trace (different trace ID, since the parent
   chain was broken to make `invoke_agent` a root in LLMObs).

## Package versions

If NuGet can't resolve `Microsoft.Extensions.AI` /
`Microsoft.Extensions.AI.OpenAI` at the pinned versions, run:

```bash
dotnet add package Microsoft.Extensions.AI
dotnet add package Microsoft.Extensions.AI.OpenAI --prerelease
```

…to update the `.csproj` to whatever's latest.
