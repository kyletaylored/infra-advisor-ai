# .NET OTel GenAI POC — verified working

Minimal ASP.NET Core + `Microsoft.Extensions.AI` reference app for
instrumenting an LLM .NET application with OpenTelemetry and shipping
to Datadog LLM Observability + APM. **Verified end-to-end:** chat and
execute_tool spans show up correctly classified in DD LLMObs, and the
whole trace tree (HTTP → chat → tool) is also visible in DD APM.

```
[ Browser + DD RUM ] --(W3C traceparent)--> [ ASP.NET POC ]
                                                 │  OTLP HTTP/protobuf
                                                 ▼
                                       [ OTel Collector ]
                                                 │   ┌─→ datadog exporter        → DD APM
                                                 │   ├─→ datadog/connector       → APM service stats
                                                 │   ├─→ otlphttp/llmobs         → DD LLM Observability
                                                 │   └─→ debug                   → stdout (local visibility)
                                                 ▼
                                            [ Datadog ]
```

The application code is **pure OTel**: only OTel GenAI semantic-convention
attributes, only `OTEL_EXPORTER_OTLP_ENDPOINT` as a knob. All Datadog-
specific routing (the `dd-otlp-source=llmobs` header, the `ml_app`
attribute that LLMObs requires for grouping) lives in the collector.
Pointing the POC at any other OTLP-compatible backend requires zero
application changes.

## Minimum-effort recipe — what it actually takes

Three layers, each with a small fixed set of changes.

### Layer 1 — Application code (`Program.cs`, 119 lines total)

The entire telemetry setup is **17 lines**. The rest is endpoints + tools.

```csharp
// 1. Azure OpenAI SDK client (singleton, M.E.AI consumes this)
builder.Services.AddSingleton(new AzureOpenAIClient(
    new Uri(endpoint), new AzureKeyCredential(apiKey)));

// 2. M.E.AI IChatClient pipeline — three decorators chained
builder.Services
    .AddChatClient(s => s.GetRequiredService<AzureOpenAIClient>()
        .GetChatClient(deployment).AsIChatClient())
    .UseFunctionInvocation()         // ⇢ execute_tool spans
    .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true);
                                     // ⇢ chat span with gen_ai.* attrs

// 3. OTel SDK — register sources, wire exporter
builder.Services.AddOpenTelemetry()
    .ConfigureResource(r => r.AddService(serviceName))
    .WithTracing(t => t
        .AddAspNetCoreInstrumentation()   // ⇢ HTTP server span (trace root)
        .AddHttpClientInstrumentation()   // ⇢ outbound HTTP (Azure OpenAI REST POST)
        .AddSource("Experimental.Microsoft.Extensions.AI")  // ← M.E.AI's source name
        .AddOtlpExporter(o => {
            o.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/traces");
            o.Protocol = OtlpExportProtocol.HttpProtobuf;
        }));
```

That's **the entire instrumentation surface in app code**. No manual
ActivitySource spans, no custom span tagging, no DD-specific attributes,
no profiler.

### Layer 1 — Packages (`InfraAdvisor.OtelPoc.csproj`, 6 references)

```xml
<!-- Microsoft.Extensions.AI: provider-neutral .NET LLM abstraction -->
<PackageReference Include="Microsoft.Extensions.AI"        Version="10.5.2" />
<PackageReference Include="Microsoft.Extensions.AI.OpenAI" Version="10.5.2" />

<!-- LLM provider SDK -->
<PackageReference Include="Azure.AI.OpenAI"                Version="2.9.0-beta.1" />

<!-- OpenTelemetry SDK + exporter -->
<PackageReference Include="OpenTelemetry.Extensions.Hosting"             Version="1.15.3" />
<PackageReference Include="OpenTelemetry.Exporter.OpenTelemetryProtocol" Version="1.15.3" />

<!-- Library auto-instrumentation: subscribe to .NET DiagnosticListener events -->
<PackageReference Include="OpenTelemetry.Instrumentation.AspNetCore" Version="1.15.2" />
<PackageReference Include="OpenTelemetry.Instrumentation.Http"       Version="1.15.1" />
```

### Layer 2 — Collector config (`experiments/otel-collector/config.yaml`)

Three things Datadog requires at the collector layer:

1. **`datadog` exporter** → APM traces, metrics, logs
2. **`otlphttp/llmobs` exporter** → DD LLM Observability OTLP intake, with
   `dd-api-key` + `dd-otlp-source=llmobs` headers
3. **`transform/llmobs` processor** → sets `ml_app` from `service.name`
   on every span's resource attributes (DD LLMObs *requires* `ml_app` to
   register the trace under an "ML application"; without it, LLMObs drops
   the trace even with perfect `gen_ai.*` attributes)

```yaml
processors:
  transform/llmobs:
    error_mode: ignore
    trace_statements:
      - context: resource
        statements:
          - set(attributes["ml_app"], attributes["service.name"])

exporters:
  datadog: { api: { key: ${env:DD_API_KEY}, site: ${env:DD_SITE} } }
  otlphttp/llmobs:
    endpoint: https://otlp.${env:DD_SITE}
    headers:
      dd-api-key: ${env:DD_API_KEY}
      dd-otlp-source: llmobs

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [transform/llmobs, batch]
      exporters: [debug, datadog, datadog/connector, otlphttp/llmobs]
```

### Layer 3 — Browser RUM (optional, `wwwroot/index.html`)

DD RUM SDK auto-injects W3C `traceparent` + `x-datadog-*` headers into
fetch() calls when `allowedTracingUrls` matches the origin — the browser-
generated trace continues into the server's OTel-instrumented trace
without any custom JS. `/config.js` endpoint serves the RUM config from
server env vars so secrets stay out of HTML.

## Span hierarchy this produces

Verified in the DD LLMObs + APM UIs from real `/chat` calls:

```
RUM browser.request                       ← root, from DD Browser SDK
└── http.server.request POST /chat        ← AspNetCore instrumentation
    └── orchestrate_tools                 ← M.E.AI internal workflow span
        ├── chat gpt-4.1-mini             ← .UseOpenTelemetry, kind: llm
        │   └── http.client.request POST  ← HttpClient instrumentation (REST to Azure OpenAI)
        ├── execute_tool <name>           ← .UseFunctionInvocation, kind: tool
        └── chat gpt-4.1-mini             ← second LLM call (after tool result)
            └── http.client.request POST  ← second HttpClient span
```

In DD LLMObs, the `chat` spans render as **LLM** kind (auto-classified
from `gen_ai.operation.name=chat`) and the `execute_tool` span renders as
**Tool** kind. Full input/output messages, tool definitions, token usage,
finish reasons, and provider/model identifiers are all populated by
Microsoft.Extensions.AI.

## Running locally

```bash
make otel-poc      # collector + POC; Ctrl+C tears both down
```

Or step-by-step:

```bash
make start-otel-collector
make run-otel-poc                # http://localhost:5005
make logs-otel-collector         # full span bodies in stdout
make stop-otel-collector
```

## Pointing at a different backend

The application is OTel-spec compliant. Any change of backend is a
collector-config change, not an app change:

```bash
# Honeycomb / Grafana Cloud / Tempo / Jaeger / self-hosted: just swap
# the exporter in experiments/otel-collector/config.yaml.

# Even DD itself if you wanted to bypass the collector for traces only:
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.us3.datadoghq.com  # (then app code would need dd-api-key headers added)
```

---

## Migration pattern for `services/agent-api-dotnet/`

The production service today uses a hand-rolled `LlmTelemetry` helper
that manually creates and tags spans (agent / router / specialist / tool
spans, custom `gen_ai.*` attributes set per-call, custom message
serialization, etc.). The POC proves that almost all of that can be
replaced by Microsoft.Extensions.AI + the right collector setup.

### Step-by-step migration

| Step | What | Where |
|---|---|---|
| 1 | Add `Microsoft.Extensions.AI` + `Microsoft.Extensions.AI.OpenAI` + `OpenTelemetry.Instrumentation.AspNetCore` + `OpenTelemetry.Instrumentation.Http` packages | `services/agent-api-dotnet/InfraAdvisor.AgentApi.csproj` |
| 2 | Replace direct `AzureOpenAIClient.GetChatClient(...)` usage with the M.E.AI `AddChatClient(...).UseFunctionInvocation().UseOpenTelemetry()` pattern | `Program.cs`, `Services/AgentService.cs` |
| 3 | Delete `Observability/LlmTelemetry.cs` and `Observability/TracingScope.cs` — all the manual span creation, tagging, message serialization | `services/agent-api-dotnet/Observability/` |
| 4 | Add `.AddSource("Experimental.Microsoft.Extensions.AI")` to the existing `WithTracing()` configuration in `TelemetrySetup.cs` | `Observability/TelemetrySetup.cs` |
| 5 | Keep `.AddSource("infra-advisor-agent-api-dotnet")` if you still want the router/specialist/workflow spans the agent emits via its own ActivitySource — but consider whether they're load-bearing now that M.E.AI emits chat+tool spans automatically | `Observability/TelemetrySetup.cs` |
| 6 | Move `ml_app` injection from the app to the collector — same `transform/llmobs` processor pattern this POC uses | Cluster's DatadogAgent CR's otelCollector config (`datadog/datadog-agent.yaml`) |
| 7 | Drop the `dd-otlp-source=llmobs` header logic and direct-to-DD intake fallback from `TelemetrySetup.cs` — let the in-cluster collector handle routing | `Observability/TelemetrySetup.cs` |

### What stays the same

- The existing OTLP export to the DD agent's OTLP receiver
  (`http://datadog-agent.datadog.svc.cluster.local:4318`) — that's the
  in-cluster equivalent of this POC's local collector.
- The Kafka eval consumer (`KafkaConsumerService.cs`) — M.E.AI works
  identically inside the Kafka background loop.
- All the conversation persistence / Redis / MCP integration code.

### What's been disproven along the way (don't repeat these dead-ends)

These were tried in this experiment and rejected with a documented reason:

1. **OTel .NET auto-instrumentation profiler injection** (`otel/autoinstrumentation-dotnet` Docker image). The image's arm64 variant ships only x64 binaries (upstream issue [#2314](https://github.com/open-telemetry/opentelemetry-dotnet-instrumentation/issues/2314)), and the Rosetta workaround triggers a QEMU/MSBuild incompatibility on Apple Silicon. Even if both were fixed, it would conflict with the DD admission controller's .NET tracer injection in K8s — two CLR profilers in one process. Library auto-instrumentation (NuGet packages above) is what we wanted from the start.

2. **Orphan-parent workaround** (start `invoke_agent` with a fresh `ActivityContext` to avoid the DD APM HTTP span as parent). Was a band-aid for a problem that doesn't exist when the collector handles routing properly — the natural parent chain (HTTP → chat → tool) works correctly in DD LLMObs as long as `ml_app` is set.

3. **Custom `LlmTelemetry` helper class** with manual ActivitySource calls, hand-rolled `gen_ai.*` attribute setting, and bespoke message serialization. Replaced entirely by `Microsoft.Extensions.AI.UseOpenTelemetry()` + `UseFunctionInvocation()`.

4. **The `sourceName: "..."` parameter on `.UseOpenTelemetry()`**. Misleading — M.E.AI's `OpenTelemetryChatClient` hardcodes the ActivitySource name to `"Experimental.Microsoft.Extensions.AI"` regardless of what's passed. Always register that exact source name in your `AddSource()` call.

## Package version notes

`Microsoft.Extensions.AI.OpenAI` was preview through 9.x; **10.5.2 is the
first stable release**. The `AsIChatClient()` extension (not the older
`AsChatClient()`) lives on `OpenAI.Chat.ChatClient`, which you get from
`AzureOpenAIClient.GetChatClient(deployment)`.
