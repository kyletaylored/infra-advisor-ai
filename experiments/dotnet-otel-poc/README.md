# .NET OTel GenAI POC

Minimal ASP.NET Core + `Microsoft.Extensions.AI` reference app for
instrumenting an LLM .NET application with OpenTelemetry. Vendor-neutral
by design — the app speaks pure OTLP and uses only OTel GenAI semantic
conventions. All backend-specific routing (Datadog, Honeycomb, Grafana
Cloud, self-hosted, …) lives in the **collector config**, not in the
application code.

```
[ Browser ]  --(W3C traceparent header)-->  [ ASP.NET POC ]
                                                 │  OTLP HTTP/protobuf
                                                 ▼
                                       [ OTel Collector ]
                                                 │  vendor-specific exporters
                                                 ▼
                                    [ Any OTel-compatible backend ]
```

## Span hierarchy emitted per `POST /chat`

```
HTTP server span              ← ASP.NET Core instrumentation
└── invoke_agent              ← manual; gen_ai.operation.name=invoke_agent
    ├── router                ← manual; non-LLM workflow step
    └── specialist-{name}     ← manual; non-LLM workflow step
        ├── prepare-context   ← manual; sub-task
        └── chat <model>      ← Microsoft.Extensions.AI .UseOpenTelemetry()
            └── execute_tool  ← Microsoft.Extensions.AI .UseFunctionInvocation()
```

The natural parent chain stays intact — the whole tree lives in **one
trace**. Browser-generated `traceparent` continues the trace from the UI
into the server (W3C Trace Context, no SDK required).

## OTel GenAI attributes used

Only attributes defined by (or proposed for) the OTel GenAI semantic
conventions. No vendor extensions:

- `gen_ai.operation.name` — `invoke_agent`, `chat`, `execute_tool`
- `gen_ai.agent.name`
- `gen_ai.conversation.id` — session linking
- `gen_ai.input.messages` / `gen_ai.output.messages` — parts-array format
- `gen_ai.request.model`
- `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens`
- `gen_ai.response.finish_reasons`

(All the `gen_ai.*` `chat` and `execute_tool` attributes come for free
from `.UseOpenTelemetry()` + `.UseFunctionInvocation()` — the app code
only sets the agent / workflow / task attributes manually.)

## Running locally

From the repo root:

```bash
make otel-poc           # starts collector + runs POC; Ctrl+C tears both down
```

Or:

```bash
make start-otel-collector   # docker, terminal 1
make run-otel-poc           # POC, terminal 2
make logs-otel-collector    # collector stdout (every span body), terminal 3
make stop-otel-collector    # cleanup
```

Open `http://localhost:5005`, type a query, hit Send.

## Pointing at a different backend

The OTLP endpoint is the only knob. Point it at any OTel-compatible
collector — vendor-specific routing is the collector's job.

```bash
# Default: local collector in experiments/otel-collector/
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318

# Or hit any other OTLP endpoint directly:
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.example.com
```

## Package versions

If NuGet can't resolve `Microsoft.Extensions.AI` /
`Microsoft.Extensions.AI.OpenAI` at the pinned versions, run:

```bash
dotnet add package Microsoft.Extensions.AI
dotnet add package Microsoft.Extensions.AI.OpenAI
```

…to update the `.csproj` to whatever's latest.
