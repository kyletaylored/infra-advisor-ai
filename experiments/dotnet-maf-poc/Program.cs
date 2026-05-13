// Microsoft Agents Framework (MAF) POC.
//
// Validates three things the M.E.AI-only POC couldn't prove:
//   1. Two-layer .UseOpenTelemetry() (chat client + agent) produces the
//      full LLMObs span hierarchy: invoke_agent → chat → execute_tool.
//   2. AgentSession maps the URL ?c=<conversationId> through to
//      gen_ai.conversation.id so DD LLMObs groups multi-turn traces.
//   3. AIContextProvider hook API works for custom memory components
//      (this POC uses an in-memory dict; production would use Redis).

using System.Collections.Concurrent;
using System.ComponentModel;
using System.Diagnostics;
using Azure;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

var builder = WebApplication.CreateBuilder(args);

// ── Config ────────────────────────────────────────────────────────────────────
var endpoint     = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT")    ?? throw new("AZURE_OPENAI_ENDPOINT not set");
var apiKey       = Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY")     ?? throw new("AZURE_OPENAI_API_KEY not set");
var deployment   = Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT")  ?? "gpt-4.1-mini";
var serviceName  = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")        ?? "infra-advisor-maf-poc";
var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT") ?? "http://localhost:4318";

// ── Tools ─────────────────────────────────────────────────────────────────────
// AIFunctionFactory reads [Description] attributes for the tool schema the
// LLM sees. Same pattern as the M.E.AI POC — once real MCP wrapping is wired
// in, these would be replaced by AIFunctions that delegate to McpClientService.
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
    };
    return quotes[Random.Shared.Next(quotes.Length)];
}

var tools = new List<AITool>
{
    AIFunctionFactory.Create(GetCurrentTime),
    AIFunctionFactory.Create(GetRandomQuote),
};

// ── Chat client pipeline (Microsoft.Extensions.AI layer) ──────────────────────
// .UseOpenTelemetry() on the chat client emits `chat` + `execute_tool` spans
// (gen_ai.operation.name=chat / execute_tool, gen_ai.input.messages, etc.)
// on the ActivitySource "Experimental.Microsoft.Extensions.AI".
builder.Services.AddSingleton(new AzureOpenAIClient(
    new Uri(endpoint), new AzureKeyCredential(apiKey)));

builder.Services.AddSingleton<IChatClient>(sp =>
    sp.GetRequiredService<AzureOpenAIClient>()
        .GetChatClient(deployment)
        .AsIChatClient()
        .AsBuilder()
        .UseFunctionInvocation()
        .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true)
        .Build());

// ── Agent (MAF layer) ─────────────────────────────────────────────────────────
// .UseOpenTelemetry() on the AGENT builder emits the additional `invoke_agent`
// span that wraps the entire turn — this is the "agent" span kind that
// DD LLMObs has been missing. The agent's source is "Microsoft.Agents.AI"
// internally, registered in AddSource below.
//
// MemoryProvider attached here validates the AIContextProvider hook API.
const string AgentName = "infra-advisor-poc-agent";

builder.Services.AddSingleton<MemoryProvider>();

builder.Services.AddSingleton<AIAgent>(sp =>
{
    var chatClient = sp.GetRequiredService<IChatClient>();
    var memory     = sp.GetRequiredService<MemoryProvider>();

    return new ChatClientAgent(chatClient, new ChatClientAgentOptions
    {
        Name = AgentName,
        ChatOptions = new ChatOptions
        {
            Instructions = "You are a friendly assistant. Use the available tools when relevant. Be concise.",
            Tools = tools,
        },
        AIContextProviders = [memory],
    })
        .AsBuilder()
        .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true)
        .Build();
});

// ── OpenTelemetry ─────────────────────────────────────────────────────────────
// Two ActivitySources to AddSource: M.E.AI's for chat+tool spans, MAF's for
// invoke_agent. Library instrumentations cover the HTTP server + outbound
// HTTP client. Same OTel pattern as the M.E.AI-only POC plus one extra source.
builder.Services.AddOpenTelemetry()
    .ConfigureResource(r => r.AddService(serviceName))
    .WithTracing(t => t
        .AddAspNetCoreInstrumentation()
        .AddHttpClientInstrumentation()
        .AddSource("Experimental.Microsoft.Extensions.AI")  // chat + execute_tool
        .AddSource("Microsoft.Agents.AI")                   // invoke_agent
        .AddOtlpExporter(o =>
        {
            o.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/traces");
            o.Protocol = OtlpExportProtocol.HttpProtobuf;
        }));

// ── App ──────────────────────────────────────────────────────────────────────
var app = builder.Build();
app.UseDefaultFiles();
app.UseStaticFiles();

// Browser RUM config (same pattern as the other POCs — values from server env)
app.MapGet("/config.js", () =>
{
    var rumAppId = Environment.GetEnvironmentVariable("DD_RUM_APPLICATION_ID") ?? "";
    var rumToken = Environment.GetEnvironmentVariable("DD_RUM_CLIENT_TOKEN")   ?? "";
    var ddSite   = Environment.GetEnvironmentVariable("DD_SITE")               ?? "us3.datadoghq.com";
    var js = $$"""
        window.RUM_CONFIG = {
          applicationId: "{{rumAppId}}",
          clientToken: "{{rumToken}}",
          site: "{{ddSite}}",
          service: "{{serviceName}}",
          env: "dev",
          version: "1.0.0",
          sessionSampleRate: 100,
          sessionReplaySampleRate: 100,
          trackUserInteractions: true,
          trackResources: true,
          trackLongTasks: true,
          defaultPrivacyLevel: "mask-user-input",
        };
        """;
    return Results.Content(js, "application/javascript");
});

// ── /chat endpoint ─────────────────────────────────────────────────────────────
// SessionStore.GetOrCreate maps the inbound conversationId → AgentSession,
// which MAF then uses to anchor gen_ai.conversation.id on every span in the
// turn. Sessions are kept in an in-memory dict for this POC; production would
// persist them via the AIContextProvider's Redis-backed state.
app.MapPost("/chat", async (
    AIAgent agent,
    SessionStore sessions,
    ChatRequest body,
    CancellationToken ct) =>
{
    var conversationId = body.ConversationId ?? body.SessionId ?? Guid.NewGuid().ToString();
    var session = await sessions.GetOrCreateAsync(agent, conversationId, ct);

    var response = await agent.RunAsync(body.Query, session, cancellationToken: ct);

    return Results.Ok(new
    {
        answer         = response.Text ?? "",
        traceId        = Activity.Current?.TraceId.ToString(),
        conversationId,
    });
});

app.Run();

// ── Types & helpers ───────────────────────────────────────────────────────────

record ChatRequest(string Query, string? SessionId, string? ConversationId);

// In-memory session cache keyed by conversation ID. Production would back this
// with Redis (or load on demand via agent.DeserializeSessionAsync).
sealed class SessionStore
{
    private readonly ConcurrentDictionary<string, AgentSession> _sessions = new();

    public async Task<AgentSession> GetOrCreateAsync(AIAgent agent, string conversationId, CancellationToken ct)
    {
        if (_sessions.TryGetValue(conversationId, out var existing)) return existing;

        var session = await agent.CreateSessionAsync(ct);
        return _sessions.GetOrAdd(conversationId, session);
    }
}

// Minimal AIContextProvider to prove the hook API fires end-to-end.
// Keep this deliberately property-access-free for now — the sample code
// (from MAF main) uses context.Session.Id and context.RequestMessages,
// but those property names aren't on the 1.5.0 stable surface. We'll
// inspect the real API shape via reflection on first invocation.
sealed class MemoryProvider : AIContextProvider
{
    protected override ValueTask<AIContext> ProvideAIContextAsync(
        InvokingContext context, CancellationToken cancellationToken = default)
    {
        Console.WriteLine(
            $"[MemoryProvider] ProvideAIContextAsync fired; " +
            $"context type={context.GetType().FullName}");
        return new(new AIContext { Instructions = null });
    }

    protected override ValueTask StoreAIContextAsync(
        InvokedContext context, CancellationToken cancellationToken = default)
    {
        Console.WriteLine(
            $"[MemoryProvider] StoreAIContextAsync fired; " +
            $"context type={context.GetType().FullName}");
        return default;
    }
}
