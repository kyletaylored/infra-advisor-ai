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
using System.Diagnostics;
using Azure;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using ModelContextProtocol.Client;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

var builder = WebApplication.CreateBuilder(args);

// ── Config ────────────────────────────────────────────────────────────────────
var endpoint      = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT")    ?? throw new("AZURE_OPENAI_ENDPOINT not set");
var apiKey        = Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY")     ?? throw new("AZURE_OPENAI_API_KEY not set");
var deployment    = Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT")  ?? "gpt-4.1-mini";
var serviceName   = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")        ?? "infra-advisor-maf-poc";
var otlpEndpoint  = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT") ?? "http://localhost:4318";
var mcpServerUrl  = Environment.GetEnvironmentVariable("MCP_SERVER_URL")           ?? "http://localhost:8000/mcp";

// ── MCP client ────────────────────────────────────────────────────────────────
// Real tools come from mcp-server-dotnet via the official ModelContextProtocol
// .NET client library. HttpClientTransport speaks Streamable-HTTP MCP — the
// same wire protocol the server uses. mcpClient.ListToolsAsync() returns
// AITool-compatible instances that spread straight into the agent's tools.
//
// For local development this points at a port-forwarded service:
//   kubectl port-forward -n infra-advisor svc/mcp-server-dotnet 8000:8000
// Override via MCP_SERVER_URL env var for any other target.
Console.WriteLine($"[mcp] connecting to {mcpServerUrl}");
var mcpTransport = new HttpClientTransport(new HttpClientTransportOptions
{
    Endpoint = new Uri(mcpServerUrl),
    Name = serviceName,
});
McpClient mcpClient;
IList<AITool> tools;
try
{
    mcpClient = await McpClient.CreateAsync(mcpTransport);
    var mcpTools = await mcpClient.ListToolsAsync();
    tools = [.. mcpTools];
    Console.WriteLine($"[mcp] connected; loaded {tools.Count} tool(s): {string.Join(", ", mcpTools.Select(t => t.Name))}");
}
catch (Exception ex)
{
    Console.Error.WriteLine($"[mcp] ERROR: failed to connect to {mcpServerUrl}: {ex.Message}");
    Console.Error.WriteLine($"[mcp]   if you're running locally, port-forward the cluster service first:");
    Console.Error.WriteLine($"[mcp]   kubectl port-forward -n infra-advisor svc/mcp-server-dotnet 8000:8000");
    throw;
}
builder.Services.AddSingleton(mcpClient);

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
const string AgentName       = "infra-advisor-poc-agent";
const string AgentSourceName = "infra-advisor-maf-poc";  // ActivitySource for MAF agent spans

builder.Services.AddSingleton<MemoryProvider>();
builder.Services.AddSingleton<SessionStore>();

builder.Services.AddSingleton<AIAgent>(sp =>
{
    var chatClient = sp.GetRequiredService<IChatClient>();
    var memory     = sp.GetRequiredService<MemoryProvider>();

    return new ChatClientAgent(chatClient, new ChatClientAgentOptions
    {
        Name = AgentName,
        ChatOptions = new ChatOptions
        {
            Instructions = "You are a helpful infrastructure consulting assistant. " +
                           "Use the available MCP tools to look up real data — bridges, water systems, " +
                           "disasters, energy infrastructure, procurement opportunities — and cite " +
                           "sources when you do. Be concise.",
            Tools = tools,
        },
        AIContextProviders = [memory],
    })
        .AsBuilder()
        // Pin sourceName so we know exactly which ActivitySource the agent
        // emits on — register that same name in AddSource below.
        .UseOpenTelemetry(sourceName: AgentSourceName, configure: cfg => cfg.EnableSensitiveData = true)
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
        .AddSource("Experimental.Microsoft.Extensions.AI")  // chat + execute_tool spans (M.E.AI internal default)
        .AddSource(AgentSourceName)                         // MAF agent spans — explicit sourceName passed above
        // Belt-and-suspenders: MAF's internal default source name isn't
        // documented for 1.5.0. Add a few candidates so the SDK captures
        // spans no matter which one MAF actually emits on.
        .AddSource("Microsoft.Agents.AI")
        .AddSource("Experimental.Microsoft.Agents.AI")
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
