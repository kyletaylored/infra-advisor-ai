# .NET OTel GenAI POC — auto-instrumented variant

Same external behavior as the sibling `experiments/dotnet-otel-poc/` —
same `POST /chat`, same `wwwroot/index.html`, same RUM-enabled UI — but
**zero `OpenTelemetry.*` packages in the csproj and zero
`AddOpenTelemetry()` code in `Program.cs`**.

All OTel instrumentation comes from the OpenTelemetry .NET
auto-instrumentation profiler attached at process startup. **The
profiler is delivered via the upstream `otel/autoinstrumentation-dotnet`
Docker image** — nothing installs to your host filesystem.

## How it works

```
┌─────────────────────────────────────┐
│ otel-autoinstr-init                 │  one-shot
│ image: otel/autoinstrumentation-    │
│        dotnet:latest                │  copies /autoinstrumentation/.
│                                     │  → shared volume `otel-auto`
└─────────────────────────────────────┘
                  │ depends_on: service_completed_successfully
                  ▼
┌─────────────────────────────────────┐
│ poc                                 │  long-running
│ build: ./Dockerfile                 │
│ mounts otel-auto at /otel-auto      │  sources /otel-auto/instrument.sh
│ entrypoint sources instrument.sh    │  → CORECLR_* env vars set
│ then execs `dotnet …`               │  → profiler attaches at startup
│                                     │
│ exports OTLP →                      │
│ host.docker.internal:4318           │
└─────────────────────────────────────┘
```

The collector keeps running under `experiments/otel-collector/` as
before; this POC reaches it via the host-published port.

## Minimum Program.cs

```csharp
builder.Services.AddSingleton(new AzureOpenAIClient(new Uri(endpoint), new AzureKeyCredential(apiKey)));

builder.Services
    .AddChatClient(s => s.GetRequiredService<AzureOpenAIClient>()
        .GetChatClient(deployment).AsIChatClient())
    .UseFunctionInvocation()
    .UseOpenTelemetry(cfg => cfg.EnableSensitiveData = true);
```

That's all the telemetry setup. The csproj contains only three
`PackageReference`s (M.E.AI, M.E.AI.OpenAI, Azure.AI.OpenAI) — no
`OpenTelemetry.*` packages at all.

## Running

From the repo root:

```bash
make otel-poc-autoinstr          # collector + POC; Ctrl+C tears down both
# or step-by-step:
make start-otel-collector
make run-otel-poc-autoinstr       # build + run the POC stack
make stop-otel-poc-autoinstr      # tear down only the POC (collector stays)
```

Open `http://localhost:5006` in a browser (note: port `5006`, not `5005`
— that's reserved for the M.E.AI-only POC so you can run both side-by-side).

First run pulls `otel/autoinstrumentation-dotnet` (~150 MB) and builds
the POC image (~250 MB SDK + 110 MB runtime base). Subsequent runs reuse
the cached layers and start in seconds.

## Expected trace per `/chat` call

```
HTTP POST /chat                          ← AspNetCore (auto-instrumented)
└── POST <azure-openai-endpoint>         ← HttpClient (auto-instrumented)
└── chat <model>                         ← Microsoft.Extensions.AI .UseOpenTelemetry()
    └── execute_tool <name>              ← Microsoft.Extensions.AI .UseFunctionInvocation()
        └── POST <azure-openai-endpoint> ← HttpClient (subsequent LLM call after tool result)
```

The HttpClient spans for the outbound Azure OpenAI POSTs are the bonus
coverage you don't get in the M.E.AI-only POC — useful for network-level
latency and seeing the actual REST round-trip.

## Comparison with the sibling M.E.AI-only POC

|                              | M.E.AI-only POC | This (auto-instrumented) |
|------------------------------|-----------------|--------------------------|
| Program.cs lines             | 119             | 103                      |
| csproj packages              | 6               | **3**                    |
| `OpenTelemetry.*` references | 3 packages      | **0**                    |
| OTel SDK code in Program.cs  | ~10 lines       | **0**                    |
| Host filesystem footprint    | nothing extra   | nothing extra (Docker)   |
| Library auto-coverage        | what you `AddSource()` | ~30 supported libraries |
| Local iteration speed        | `dotnet run` (~2s) | `docker compose` (~5–10s after first build) |

## Production caveat (not blocking this POC)

In AKS, the Datadog admission controller injects the **DD .NET tracer**
into pods in the `infra-advisor` namespace — that's also a CLR profiler.
Running two CLR profilers in one process produces conflicting span
ownership and sampling decisions. If we promote this approach to AKS,
the `DatadogAgent.spec.features.apm.instrumentation.targets`
namespaceSelector would need to exclude the workload running OTel
auto-instrumentation.
