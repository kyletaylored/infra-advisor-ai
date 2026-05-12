// Minimal .NET OTel GenAI proof-of-concept.
//
// Goal: take an existing LLM .NET application, instrument it with OTel
// using the GenAI semantic conventions, and ship it to ANY OTLP-compatible
// backend. No vendor-specific code, headers, or attributes live here —
// vendor routing (Datadog, Honeycomb, Grafana Cloud, self-hosted, …) is
// the collector's job, not the app's.
//
// What this exercises end-to-end:
//   - Microsoft.Extensions.AI's .UseOpenTelemetry() decorator emits OTel
//     GenAI-spec `chat` spans (gen_ai.input.messages parts array,
//     gen_ai.usage.input_tokens / output_tokens, gen_ai.response.finish_reasons).
//   - .UseFunctionInvocation() emits `execute_tool` spans per tool call.
//   - Manual ActivitySource spans for the higher-level agent flow
//     (invoke_agent → router → specialist → task), all using OTel-native
//     `gen_ai.*` attributes per the GenAI semantic conventions.
//   - ASP.NET Core instrumentation exports the HTTP server span, so the
//     full chain — HTTP → invoke_agent → workflows → llm → tool — lives in
//     one trace tree.
//   - W3C `traceparent` propagation from the browser (no SDK) so the
//     browser-generated trace_id continues into the server.

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
var serviceName   = Opt("OTEL_SERVICE_NAME", "otel-genai-poc");
var otlpEndpoint  = Opt("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318");

// One ActivitySource for everything — our manual spans AND the
// Microsoft.Extensions.AI OpenTelemetry decorator. A single AddSource()
// call then covers the whole pipeline.
const string SourceName = "otel-genai-poc";
var activitySource = new ActivitySource(SourceName);
builder.Services.AddSingleton(activitySource);

// ── IChatClient pipeline ──────────────────────────────────────────────────────
// AsIChatClient() adapts the underlying OpenAI ChatClient → IChatClient.
// UseFunctionInvocation() runs the tool-calling loop AND emits execute_tool
//   spans automatically.
// UseOpenTelemetry() emits the gen_ai.* chat span around the LLM call.
builder.Services.AddSingleton<IChatClient>(_ =>
{
    var azureClient = new AzureOpenAIClient(
        new Uri(azureEndpoint),
        new AzureKeyCredential(azureApiKey));

    return new ChatClientBuilder(azureClient.GetChatClient(deployment).AsIChatClient())
        .UseFunctionInvocation()
        .UseOpenTelemetry(sourceName: SourceName, configure: cfg =>
        {
            // Emit full prompt/completion content into gen_ai.input.messages /
            // gen_ai.output.messages.
            cfg.EnableSensitiveData = true;
        })
        .Build();
});

// ── OpenTelemetry ─────────────────────────────────────────────────────────────
// Single OTLP endpoint for traces, metrics, and logs. The collector at
// the other end is responsible for any backend-specific routing or header
// injection — no vendor knowledge bakes into the application.
void ConfigureOtlp(OtlpExporterOptions otlp, string signal)
{
    otlp.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/{signal}");
    otlp.Protocol = OtlpExportProtocol.HttpProtobuf;
}

builder.Services.AddOpenTelemetry()
    .ConfigureResource(r => r
        .AddService(serviceName)
        .AddAttributes(new Dictionary<string, object>
        {
            ["deployment.environment"] = Opt("OTEL_DEPLOYMENT_ENV", "dev"),
            ["service.version"]        = Opt("OTEL_SERVICE_VERSION", "1.0.0"),
        }))
    .WithTracing(t => t
        // ASP.NET Core HTTP server spans — exporting these makes the HTTP
        // request the natural root of every chat trace.
        .AddAspNetCoreInstrumentation()
        .AddHttpClientInstrumentation()
        .AddSource(SourceName)
        .SetSampler(new AlwaysOnSampler())
        .AddOtlpExporter(otlp => ConfigureOtlp(otlp, "traces")))
    .WithMetrics(m => m
        .AddAspNetCoreInstrumentation()
        .AddHttpClientInstrumentation()
        .AddMeter(SourceName)
        .AddOtlpExporter(otlp => ConfigureOtlp(otlp, "metrics")));

builder.Logging.AddSimpleConsole();
builder.Logging.AddOpenTelemetry(logging =>
{
    logging.IncludeFormattedMessage = true;
    logging.IncludeScopes           = true;
    logging.AddOtlpExporter(otlp => ConfigureOtlp(otlp, "logs"));
});

// ── Tools (AIFunctions) ───────────────────────────────────────────────────────
// Each call to one of these from the chat loop produces an execute_tool
// span via UseFunctionInvocation — no per-tool span code required.
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

// ── Static UI ────────────────────────────────────────────────────────────────
app.UseDefaultFiles();
app.UseStaticFiles();

// ── Chat endpoint ─────────────────────────────────────────────────────────────
app.MapPost("/chat", async (ChatRequest req, IChatClient chatClient) =>
{
    // invoke_agent is a child of the ASP.NET HTTP server span (Activity.Current
    // when this endpoint runs). The natural parent chain gives a single trace
    // tree: HTTP → invoke_agent → router → specialist → chat → execute_tool.
    using var agentSpan = activitySource.StartActivity(
        "invoke_agent", ActivityKind.Internal);
    agentSpan?.SetTag("gen_ai.operation.name", "invoke_agent");
    agentSpan?.SetTag("gen_ai.agent.name", "otel-genai-poc-agent");
    agentSpan?.SetTag("gen_ai.conversation.id", req.SessionId ?? "anonymous");

    // ── Router workflow ───────────────────────────────────────────────────────
    // Trivial heuristic stand-in for an LLM-driven router. Just a regular
    // ActivitySource span — no gen_ai.operation.name since routing is not
    // a GenAI-spec operation; backends can render this however they like.
    string specialist;
    using (var routerSpan = activitySource.StartActivity("router", ActivityKind.Internal))
    {
        routerSpan?.SetTag("gen_ai.conversation.id", req.SessionId ?? "anonymous");

        specialist = req.Query.Contains("time", StringComparison.OrdinalIgnoreCase)
            ? "time-specialist"
            : "general";

        routerSpan?.SetTag("router.specialist", specialist);
    }

    // ── Specialist workflow (wraps the LLM + tool loop) ──────────────────────
    string answer;
    using (var specialistSpan = activitySource.StartActivity(
        $"specialist-{specialist}", ActivityKind.Internal))
    {
        specialistSpan?.SetTag("gen_ai.conversation.id", req.SessionId ?? "anonymous");
        specialistSpan?.SetTag("specialist", specialist);

        // Sub-task: context prep, a non-LLM step inside the workflow.
        using (var prepSpan = activitySource.StartActivity(
            "prepare-context", ActivityKind.Internal))
        {
            prepSpan?.SetTag("gen_ai.conversation.id", req.SessionId ?? "anonymous");
            await Task.Delay(25);  // simulate prep work
        }

        // ── llm + tool spans emitted by Microsoft.Extensions.AI ────────────
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

    return Results.Ok(new PocChatResponse(answer, Activity.Current?.TraceId.ToString()));
});

app.Run();

// Record types must be at the bottom (after top-level statements).
// PocChatResponse avoids a name collision with Microsoft.Extensions.AI.ChatResponse.
record ChatRequest(string Query, string? SessionId);
record PocChatResponse(string Answer, string? TraceId);
