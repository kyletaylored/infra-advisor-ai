// Minimal .NET OTel GenAI proof-of-concept for Datadog LLM Observability.
//
// What this POC validates end-to-end:
//   - Microsoft.Extensions.AI emits OTel GenAI semantic-convention `chat` spans
//     (gen_ai.input.messages parts array, gen_ai.usage.input_tokens / output_tokens,
//     gen_ai.response.finish_reasons, etc.) via .UseOpenTelemetry().
//   - .UseFunctionInvocation() turns each tool call into a properly-tagged
//     `execute_tool` span → "tool" kind in DD LLMObs.
//   - Manual ActivitySource spans demonstrate the other LLMObs kinds:
//     "agent"   → gen_ai.operation.name = "invoke_agent"
//     "workflow"→ dd.llmobs.span.kind  = "workflow"  (router / specialist)
//     "task"    → dd.llmobs.span.kind  = "task"      (sub-step within workflow)
//   - OTLP HTTP/protobuf export direct to https://otlp.${DD_SITE}/v1/traces
//     with the dd-otlp-source=llmobs header — the same path the main service uses.
//   - Browser RUM sessions correlate to the same LLMObs traces via the
//     gen_ai.conversation.id tag (set from the RUM-generated sessionId).

using System.ComponentModel;
using System.Diagnostics;
using Azure;
using Azure.AI.OpenAI;
using Microsoft.Extensions.AI;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Logs;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

var builder = WebApplication.CreateBuilder(args);

// ── Config ────────────────────────────────────────────────────────────────────
string Req(string k) => Environment.GetEnvironmentVariable(k)
    ?? throw new InvalidOperationException($"Required env var '{k}' is not set.");
string Opt(string k, string fallback) =>
    Environment.GetEnvironmentVariable(k) ?? fallback;

var azureEndpoint = Req("AZURE_OPENAI_ENDPOINT");
var azureApiKey   = Req("AZURE_OPENAI_API_KEY");
var deployment    = Opt("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini");
var ddApiKey      = Req("DD_API_KEY");
var ddSite        = Opt("DD_SITE", "us3.datadoghq.com");
var serviceName   = Opt("OTEL_SERVICE_NAME", "infra-advisor-otel-poc");

// One ActivitySource for everything — our manual agent/workflow/task spans AND
// the Microsoft.Extensions.AI OpenTelemetry decorator. A single AddSource() call
// then covers the whole pipeline.
const string SourceName = "infra-advisor-otel-poc";
var activitySource = new ActivitySource(SourceName);
builder.Services.AddSingleton(activitySource);

// ── IChatClient pipeline ──────────────────────────────────────────────────────
// AsChatClient() adapts AzureOpenAIClient → IChatClient.
// UseFunctionInvocation() handles the tool-calling ReAct loop AND emits
//   execute_tool spans automatically (no manual span code per tool).
// UseOpenTelemetry() wraps the inner chat call in a gen_ai.* span.
builder.Services.AddSingleton<IChatClient>(_ =>
{
    var azureClient = new AzureOpenAIClient(
        new Uri(azureEndpoint),
        new AzureKeyCredential(azureApiKey));

    // Two-step adaptation: AzureOpenAIClient → OpenAI ChatClient → IChatClient.
    // .AsIChatClient() is the M.E.AI.OpenAI extension on OpenAI.Chat.ChatClient
    // (not on AzureOpenAIClient itself).
    return new ChatClientBuilder(azureClient.GetChatClient(deployment).AsIChatClient())
        .UseFunctionInvocation()
        .UseOpenTelemetry(sourceName: SourceName, configure: cfg =>
        {
            // Emit full prompt/completion content into gen_ai.input.messages /
            // gen_ai.output.messages so the DD LLMObs UI shows the conversation.
            cfg.EnableSensitiveData = true;
        })
        .Build();
});

// ── OpenTelemetry ─────────────────────────────────────────────────────────────
// Default target is the local OTel Collector (experiments/otel-collector/).
// The collector handles dd-api-key / dd-otlp-source routing + fan-out to APM
// and LLMObs. To bypass the collector and send straight to DD's OTLP intake,
// set OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.us3.datadoghq.com (or whichever
// site you're on). The host-suffix check below adds the dd-* headers
// automatically in that case so traces still reach LLMObs.
var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT")
    ?? "http://localhost:4318";
var isDirectToDatadog = Uri.TryCreate(otlpEndpoint, UriKind.Absolute, out var otlpUri)
    && otlpUri.Host.EndsWith("datadoghq.com", StringComparison.OrdinalIgnoreCase);

// Local helper so traces/metrics/logs share one endpoint-resolution rule.
// `signal` is the OTLP path suffix: "traces", "metrics", or "logs".
void ConfigureOtlp(OtlpExporterOptions otlp, string signal)
{
    otlp.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/{signal}");
    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
    if (isDirectToDatadog)
    {
        // dd-otlp-source=llmobs only applies to the traces signal — metrics
        // and logs would never go direct to DD's OTLP intake anyway in practice
        // (no LLMObs counterpart), but we keep dd-api-key on them for parity.
        var headers = $"dd-api-key={ddApiKey}";
        if (signal == "traces") headers += ",dd-otlp-source=llmobs";
        otlp.Headers = headers;
    }
}

// AlwaysOnSampler forces recording regardless of parent context — same fix
// pattern that was needed in the main service after we discovered the DD
// bridge could mark HTTP spans as Recorded=0.
builder.Services.AddOpenTelemetry()
    .ConfigureResource(r => r
        .AddService(serviceName)
        .AddAttributes(new Dictionary<string, object>
        {
            ["deployment.environment"] = Opt("DD_ENV", "dev"),
            ["service.version"]        = Opt("DD_VERSION", "1.0.0"),
        }))
    .WithTracing(t => t
        .AddSource(SourceName)
        .SetSampler(new AlwaysOnSampler())
        .AddOtlpExporter(otlp => ConfigureOtlp(otlp, "traces")))
    .WithMetrics(m => m
        .AddAspNetCoreInstrumentation()
        .AddHttpClientInstrumentation()
        .AddMeter(SourceName)
        .AddOtlpExporter(otlp => ConfigureOtlp(otlp, "metrics")));

// Logs: route ILogger output through the OTel logging provider so the same
// OTLP exporter ships console logs to the collector. Console output is also
// kept for local visibility.
builder.Logging.AddSimpleConsole();
builder.Logging.AddOpenTelemetry(logging =>
{
    logging.IncludeFormattedMessage = true;
    logging.IncludeScopes           = true;
    logging.AddOtlpExporter(otlp => ConfigureOtlp(otlp, "logs"));
});

// ── Tools (AIFunctions) ───────────────────────────────────────────────────────
// Each call to one of these from the chat loop produces an execute_tool span
// via UseFunctionInvocation, no per-tool span code required.
[Description("Returns the current UTC time as an ISO-8601 string.")]
static string GetCurrentTime() => DateTime.UtcNow.ToString("o");

[Description("Returns a random inspirational quote.")]
static string GetRandomQuote()
{
    string[] quotes =
    {
        "The only way to do great work is to love what you do. — Steve Jobs",
        "Stay hungry, stay foolish. — Whole Earth Catalog",
        "In the middle of difficulty lies opportunity. — Albert Einstein",
        "Make it work, make it right, make it fast. — Kent Beck",
    };
    return quotes[Random.Shared.Next(quotes.Length)];
}

var tools = new List<AITool>
{
    AIFunctionFactory.Create(GetCurrentTime),
    AIFunctionFactory.Create(GetRandomQuote),
};

var app = builder.Build();

// ── Static UI + RUM config ────────────────────────────────────────────────────
app.UseDefaultFiles();
app.UseStaticFiles();

// Serves the RUM config from server env vars so we don't bake secrets into the
// HTML. The browser script reads window.RUM_CONFIG and forwards it to DD_RUM.init().
app.MapGet("/config.js", () =>
{
    var rumAppId = Opt("DD_RUM_APPLICATION_ID", "");
    var rumToken = Opt("DD_RUM_CLIENT_TOKEN", "");
    var js =
        $$"""
        window.RUM_CONFIG = {
          applicationId: "{{rumAppId}}",
          clientToken: "{{rumToken}}",
          site: "{{ddSite}}",
          service: "{{serviceName}}",
          env: "{{Opt("DD_ENV", "dev")}}",
          version: "{{Opt("DD_VERSION", "1.0.0")}}",
          sessionSampleRate: 100,
          sessionReplaySampleRate: 100,
          trackUserInteractions: true,
          trackResources: true,
          trackLongTasks: true,
          defaultPrivacyLevel: "mask-user-input"
        };
        """;
    return Results.Content(js, "application/javascript");
});

// ── Chat endpoint ─────────────────────────────────────────────────────────────
app.MapPost("/chat", async (ChatRequest req, IChatClient chatClient) =>
{
    // The ASP.NET request currently has its own Activity (HTTP span). We want
    // invoke_agent to be a true root in the OTLP/LLMObs export — otherwise DD
    // LLMObs sees a dangling parentSpanId and drops the trace.
    // Tag the original APM trace ID for cross-product correlation in the DD UI.
    var apmTraceId = Activity.Current?.TraceId.ToString();
    var rootContext = new ActivityContext(
        ActivityTraceId.CreateRandom(),
        default,                      // zero parent SpanId → true root in OTLP proto
        ActivityTraceFlags.Recorded,
        isRemote: false);

    using var agentSpan = activitySource.StartActivity(
        "invoke_agent", ActivityKind.Internal, rootContext);
    agentSpan?.SetTag("gen_ai.operation.name", "invoke_agent");
    agentSpan?.SetTag("gen_ai.agent.name", "otel-poc-agent");
    agentSpan?.SetTag("gen_ai.conversation.id", req.SessionId ?? "anonymous");
    agentSpan?.SetTag("dd.llmobs.span.kind", "agent");
    agentSpan?.SetTag("ml_app", serviceName);
    if (apmTraceId is not null) agentSpan?.SetTag("apm.trace_id", apmTraceId);

    // ── workflow: router ─────────────────────────────────────────────────────
    // Trivial heuristic stand-in for an LLM-driven router. The span demonstrates
    // the "workflow" kind without burning a router LLM call in the POC.
    string specialist;
    using (var routerSpan = activitySource.StartActivity("router", ActivityKind.Internal))
    {
        routerSpan?.SetTag("dd.llmobs.span.kind", "workflow");
        routerSpan?.SetTag("ml_app", serviceName);
        routerSpan?.SetTag("gen_ai.conversation.id", req.SessionId ?? "anonymous");

        specialist = req.Query.Contains("time", StringComparison.OrdinalIgnoreCase)
            ? "time-specialist"
            : "general";

        routerSpan?.SetTag("router.specialist", specialist);
    }

    // ── workflow: specialist (wraps the LLM + tool loop) ─────────────────────
    string answer;
    using (var specialistSpan = activitySource.StartActivity(
        $"specialist-{specialist}", ActivityKind.Internal))
    {
        specialistSpan?.SetTag("dd.llmobs.span.kind", "workflow");
        specialistSpan?.SetTag("ml_app", serviceName);
        specialistSpan?.SetTag("gen_ai.conversation.id", req.SessionId ?? "anonymous");
        specialistSpan?.SetTag("specialist", specialist);

        // ── task: context-prep sub-step ─────────────────────────────────────
        using (var prepSpan = activitySource.StartActivity(
            "prepare-context", ActivityKind.Internal))
        {
            prepSpan?.SetTag("dd.llmobs.span.kind", "task");
            prepSpan?.SetTag("ml_app", serviceName);
            prepSpan?.SetTag("gen_ai.conversation.id", req.SessionId ?? "anonymous");
            await Task.Delay(25);  // simulate a short prep step
        }

        // ── llm + tool spans (emitted by Microsoft.Extensions.AI) ───────────
        var messages = new List<ChatMessage>
        {
            new(ChatRole.System,
                "You are a friendly assistant. Use the available tools when relevant. " +
                "Be concise."),
            new(ChatRole.User, req.Query),
        };
        var options = new ChatOptions { Tools = tools };
        var response = await chatClient.GetResponseAsync(messages, options);
        answer = response.Text ?? "";

        specialistSpan?.SetTag("answer.length", answer.Length);
    }

    return Results.Ok(new PocChatResponse(answer, apmTraceId));
});

app.Run();

// Record types must be at the bottom (after top-level statements).
// PocChatResponse avoids a name collision with Microsoft.Extensions.AI.ChatResponse.
record ChatRequest(string Query, string? SessionId);
record PocChatResponse(string Answer, string? ApmTraceId);
