# .NET OTel GenAI POC — auto-instrumented variant

Same external behavior as the sibling `experiments/dotnet-otel-poc/` —
same `POST /chat`, same `wwwroot/index.html`, same RUM-enabled UI —
but **zero `OpenTelemetry.*` packages in the csproj and zero
`AddOpenTelemetry()` code in `Program.cs`**.

All instrumentation comes from the OpenTelemetry .NET
auto-instrumentation profiler that attaches to the .NET runtime at
process startup. The profiler:

- Listens to every `ActivitySource` in the process (no `AddSource()`
  needed — `Microsoft.Extensions.AI`'s spans are picked up automatically).
- Auto-instruments ASP.NET Core, HttpClient, the Azure SDK, and ~30
  other libraries (full list: https://github.com/open-telemetry/opentelemetry-dotnet-instrumentation#supported-libraries).
- Configures the OTLP exporter from standard `OTEL_EXPORTER_OTLP_*`
  env vars.

The application code is responsible for **one thing only**: building the
`IChatClient` middleware pipeline with `.UseFunctionInvocation()` +
`.UseOpenTelemetry()`. Microsoft.Extensions.AI emits the
`gen_ai.*` spans; the profiler routes them.

## Minimum Program.cs (this POC)

```csharp
builder.Services.AddSingleton(new AzureOpenAIClient(new Uri(endpoint), new AzureKeyCredential(apiKey)));

builder.Services
    .AddChatClient(s => s.GetRequiredService<AzureOpenAIClient>()
        .GetChatClient(deployment).AsIChatClient())
    .UseFunctionInvocation()
    .UseOpenTelemetry(cfg => cfg.EnableSensitiveData = true);

// + tools + /chat endpoint + /config.js + app.Run()
```

That's it for the OTel setup. Compare to the sibling POC's
`Program.cs` which has an explicit `AddOpenTelemetry().WithTracing()`
block with `AddSource()`, `AddAspNetCoreInstrumentation()`, and
`AddOtlpExporter()`. All of that is now the profiler's job.

## One-time install (≈30 seconds)

```bash
curl -sSfL https://github.com/open-telemetry/opentelemetry-dotnet-instrumentation/releases/latest/download/otel-dotnet-auto-install.sh -O
sh ./otel-dotnet-auto-install.sh
```

This drops the profiler into `~/.otel-dotnet-auto/` and creates an
`instrument.sh` you source to set the required `CORECLR_*` env vars
before running a .NET process.

## Running

From the repo root:

```bash
make otel-poc-autoinstr           # starts collector + runs this POC; Ctrl+C tears both down
# or
make run-otel-poc-autoinstr       # POC only (collector must already be running)
```

The Makefile target sources `~/.otel-dotnet-auto/instrument.sh`
internally, so once the one-time install is done you don't need to
think about env vars.

## Expected trace per `/chat` call

```
HTTP POST /chat                          ← AspNetCore (auto-instrumented)
└── POST {azure_openai_endpoint}         ← HttpClient (auto-instrumented)
└── chat <model>                         ← Microsoft.Extensions.AI .UseOpenTelemetry()
    └── execute_tool get_current_time    ← Microsoft.Extensions.AI .UseFunctionInvocation() (when called)
        └── POST {azure_openai_endpoint} ← HttpClient (subsequent LLM call after tool result)
```

The HttpClient spans for the outbound Azure OpenAI POSTs are bonus
coverage — they're what the M.E.AI-only POC doesn't have. They confirm
the actual network round-trip latency and any HTTP-level failures.

## Production caveat (not blocking this POC)

In AKS the Datadog admission controller injects the **DD .NET tracer**
into pods in the `infra-advisor` namespace — that's also a CLR
profiler. Running the OTel auto-instrumentation profiler in the same
process as the DD profiler is a known conflict that produces unpredictable
span ownership and sampling decisions.

If we promote this approach to AKS later, we'd configure the DD agent
to skip injection on a specific namespace (e.g. `apm.instrumentation.targets`
namespaceSelector excluding the auto-instrumented workload).
