using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Azure;
using Azure.AI.OpenAI;
using InfraAdvisor.AgentApi.Models;
using InfraAdvisor.AgentApi.Observability;
using InfraAdvisor.AgentApi.Services;
using InfraAdvisor.AgentApi.Services.Evaluators;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using ModelContextProtocol.Client;
using StackExchange.Redis;

var builder = WebApplication.CreateBuilder(args);

// ── Environment variable helpers ──────────────────────────────────────────────
static string Env(string key, string? fallback = null) =>
    Environment.GetEnvironmentVariable(key)
    ?? fallback
    ?? throw new InvalidOperationException($"Required environment variable '{key}' is not set.");

static string EnvOr(string key, string fallback) =>
    Environment.GetEnvironmentVariable(key) ?? fallback;

// Prefer the x-datadog-trace-id request header (RUM injects the authoritative
// 64-bit decimal DD trace ID). Fall back to converting OTel lower-64-bit hex
// to decimal so direct API tests (no RUM) still get a usable identifier.
static string? GetDdTraceId(HttpContext ctx, Activity? activity)
{
    var header = ctx.Request.Headers["x-datadog-trace-id"].FirstOrDefault();
    if (!string.IsNullOrWhiteSpace(header)) return header;
    var hex = activity?.TraceId.ToString();
    if (hex is not { Length: 32 }) return hex;
    return ulong.TryParse(hex[16..], System.Globalization.NumberStyles.HexNumber, null, out var lo)
        ? lo.ToString() : hex;
}

static string? GetDdSpanId(Activity? activity)
{
    var hex = activity?.SpanId.ToString();
    if (hex is null) return null;
    return ulong.TryParse(hex, System.Globalization.NumberStyles.HexNumber, null, out var id)
        ? id.ToString() : hex;
}

// ── Configuration ─────────────────────────────────────────────────────────────
var azureEndpoint = Env("AZURE_OPENAI_ENDPOINT");
var azureApiKey   = Env("AZURE_OPENAI_API_KEY");
var azureDeployment = EnvOr("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini");
var azureEmbeddingDeployment = EnvOr("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small");
var availableModelsRaw = EnvOr("AVAILABLE_MODELS", "gpt-4.1-mini");
var mcpServerUrl = EnvOr("MCP_SERVER_URL", "http://mcp-server-dotnet.infra-advisor.svc.cluster.local:8000/mcp");
var redisHost = EnvOr("REDIS_HOST", "redis.infra-advisor.svc.cluster.local");
var redisPort = int.Parse(EnvOr("REDIS_PORT", "6379"));
var kafkaBootstrapServers = EnvOr("KAFKA_BOOTSTRAP_SERVERS", "kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092");

Environment.SetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT", azureDeployment);
Environment.SetEnvironmentVariable("KAFKA_BOOTSTRAP_SERVERS", kafkaBootstrapServers);

// ── OpenTelemetry + Logging ───────────────────────────────────────────────────
TelemetrySetup.Configure(builder);

// ── AppState ─────────────────────────────────────────────────────────────────
builder.Services.AddSingleton(new AppState());

// ── Redis ─────────────────────────────────────────────────────────────────────
builder.Services.AddSingleton<IConnectionMultiplexer>(_ =>
{
    var cfg = new ConfigurationOptions
    {
        EndPoints = { $"{redisHost}:{redisPort}" },
        AbortOnConnectFail = false,
        ConnectTimeout = 5000,
        SyncTimeout = 5000,
    };
    try { return ConnectionMultiplexer.Connect(cfg); }
    catch (Exception ex)
    {
        var loggerFactory = LoggerFactory.Create(b => b.AddConsole());
        loggerFactory.CreateLogger("Redis").LogWarning("Redis connection failed: {Error}", ex.Message);
        return ConnectionMultiplexer.Connect(cfg);
    }
});

// ── Azure OpenAI client (used by both M.E.AI's IChatClient and SuggestionService) ─
builder.Services.AddSingleton(_ => new AzureOpenAIClient(
    new Uri(azureEndpoint), new AzureKeyCredential(azureApiKey)));

// ── MCP client — connect once at startup, list tools, register both ──────────
// We connect synchronously before builder.Build() so the tools list is
// captured into the agent factory closure below. Connection failure aborts
// startup with a clear log line.
McpClient mcpClient;
IList<AITool> mcpTools;
try
{
    var transport = new HttpClientTransport(new HttpClientTransportOptions
    {
        Endpoint = new Uri(mcpServerUrl),
        Name = "infra-advisor-agent-api-dotnet",
    });
    mcpClient = await McpClient.CreateAsync(transport);
    var listed = await mcpClient.ListToolsAsync();
    mcpTools = [.. listed];
    Console.WriteLine($"[mcp] connected to {mcpServerUrl}; loaded {mcpTools.Count} tool(s): {string.Join(", ", listed.Select(t => t.Name))}");
}
catch (Exception ex)
{
    Console.Error.WriteLine($"[mcp] FATAL: failed to connect to {mcpServerUrl}: {ex.Message}");
    throw;
}
builder.Services.AddSingleton(mcpClient);

// ── IChatClient pipeline (M.E.AI) ─────────────────────────────────────────────
// .UseFunctionInvocation() runs the tool-call loop and emits execute_tool spans.
// .UseOpenTelemetry()  emits chat spans on the "Experimental.Microsoft.Extensions.AI"
// ActivitySource (registered in TelemetrySetup.cs).
builder.Services.AddSingleton<IChatClient>(sp =>
    sp.GetRequiredService<AzureOpenAIClient>()
        .GetChatClient(azureDeployment)
        .AsIChatClient()
        .AsBuilder()
        .UseFunctionInvocation()
        .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true)
        .Build());

// ── IEmbeddingGenerator pipeline (M.E.AI) ─────────────────────────────────────
// Azure OpenAI embedding deployment behind the M.E.AI provider-neutral
// interface. .UseOpenTelemetry() emits an "embeddings" span (gen_ai.operation
// .name=embeddings) on the same Experimental.Microsoft.Extensions.AI source
// as chat/tool spans — DD LLMObs auto-classifies it as the "embedding" kind.
builder.Services.AddSingleton<IEmbeddingGenerator<string, Embedding<float>>>(sp =>
    sp.GetRequiredService<AzureOpenAIClient>()
        .GetEmbeddingClient(azureEmbeddingDeployment)
        .AsIEmbeddingGenerator()
        .AsBuilder()
        .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true)
        .Build());

// ── Agent (MAF) ───────────────────────────────────────────────────────────────
// Single ChatClientAgent with all MCP tools. The model picks which tools
// to call per turn. .UseOpenTelemetry(sourceName:) emits the invoke_agent
// span on the ActivitySource registered in TelemetrySetup.cs.
const string AgentSystemPrompt =
    "You are InfraAdvisor, a technical AI assistant for consultants across " +
    "AEC/O&M (Architecture, Engineering, Construction / Operations & Maintenance) " +
    "practice areas at a global infrastructure consulting firm.\n\n" +
    "Your expertise spans the full AEC/O&M project lifecycle: feasibility and planning, " +
    "civil and structural engineering (bridges, highways, rail), MEP and environmental systems " +
    "(water, wastewater, energy), construction project delivery, asset operations and maintenance, " +
    "and management advisory (program management, BD, risk, compliance).\n\n" +
    "You have access to tools covering bridges (FHWA NBI), disasters (FEMA), energy (EIA/ERCOT), " +
    "water systems (EPA SDWIS/TWDB), Texas transportation (TxDOT), firm knowledge base, " +
    "document drafting, and federal procurement intelligence (SAM.gov, USASpending.gov).\n\n" +
    "Guidelines:\n" +
    "1. Always cite the data source for factual claims (NBI structure numbers, PWSID, EIA plant IDs, " +
    "FEMA declaration IDs, USASpending award IDs, SAM.gov solicitation numbers).\n" +
    "2. Sort assets by descending risk: bridges by ascending sufficiency rating; water systems by " +
    "descending violation count.\n" +
    "3. Flag material risks explicitly — scour vulnerability, load rating deficiencies, repeat flood " +
    "events, SDWA violations, grid stress periods.\n" +
    "4. For business development queries, always call get_contract_awards before get_procurement_opportunities " +
    "— understanding who won similar work informs positioning for open opportunities.\n" +
    "5. When search_web_procurement returns results, flag medium-confidence extractions explicitly.\n" +
    "6. NEVER ask the user for a date range — procurement tools default to the last 12 months automatically.\n" +
    "7. For document drafts, call search_project_knowledge first for relevant templates and prior project context.\n" +
    "8. Do not speculate about asset conditions not in the data — say \"not available in the dataset\".\n" +
    "9. Respond in the same language the user writes in. Keep responses concise for data lookups; " +
    "detailed for engineering analysis and document drafts.";

builder.Services.AddSingleton<AIAgent>(sp =>
    new ChatClientAgent(
            sp.GetRequiredService<IChatClient>(),
            new ChatClientAgentOptions
            {
                Name = "infra-advisor",
                ChatOptions = new ChatOptions
                {
                    Instructions = AgentSystemPrompt,
                    Tools = mcpTools,
                },
            })
        .AsBuilder()
        .UseOpenTelemetry(sourceName: TelemetrySetup.ActivitySourceName,
                          configure: cfg => cfg.EnableSensitiveData = true)
        .Build());

// ── Prompt tracking + agent-span capture ──────────────────────────────────────
// One ActivityListener does two jobs:
//   1. Stamps `_dd.ml_obs.prompt_tracking` (JSON metadata: name, version,
//      template) on every chat + invoke_agent span. DD's Prompt Tracking
//      UI reads this for per-version metrics + A/B comparison.
//   2. Captures the invoke_agent span's (trace_id, span_id) into an
//      AsyncLocal so AgentService can attach external-eval scores to the
//      AGENT span (not the HTTP root) — DD requires both IDs on the
//      eval-metric API's join_on.span field.
static string ShortContentHash(string text)
{
    var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(text));
    return Convert.ToHexString(bytes).Substring(0, 8).ToLowerInvariant();
}

var promptVersion = "v1-" + ShortContentHash(AgentSystemPrompt);
var promptTrackingJson = JsonSerializer.Serialize(new
{
    name = "infra-advisor-system",
    version = promptVersion,
    template = AgentSystemPrompt,
    variables = new Dictionary<string, object>(),
});

ActivitySource.AddActivityListener(new ActivityListener
{
    ShouldListenTo = source =>
        source.Name == "Experimental.Microsoft.Extensions.AI" ||
        source.Name == TelemetrySetup.ActivitySourceName,
    ActivityStarted = activity =>
    {
        if (activity.OperationName is "invoke_agent" or "chat")
            activity.SetTag("_dd.ml_obs.prompt_tracking", promptTrackingJson);
        if (activity.OperationName == "invoke_agent")
            AgentSpanContext.Capture(activity);
    },
    Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllData,
});
Console.WriteLine($"[prompt-tracking] registered system prompt {promptVersion} " +
                  $"({AgentSystemPrompt.Length} chars)");

// ── Business metrics meter ────────────────────────────────────────────────────
// Shared meter for endpoint-level counters (conversation + tool counters
// live in AgentService since they need response data; feedback counter is
// emitted from the /feedback endpoint below). Same name as the OTel meter
// pipeline already AddMeter's, so DD picks them up via OTLP automatically.
var bizMeter = new Meter(TelemetrySetup.ActivitySourceName);
var feedbackCounter = bizMeter.CreateCounter<long>(
    "infra_advisor.feedback.submitted",
    description: "User feedback submissions via /feedback. Tagged with rating.");

// ── Core services ─────────────────────────────────────────────────────────────
builder.Services.AddSingleton<MemoryService>();
builder.Services.AddSingleton<AgentSessionStore>();
builder.Services.AddSingleton<RetrievalService>();
builder.Services.AddHttpClient<DatadogEvalsClient>();
builder.Services.AddSingleton<IResponseEvaluator, CitationPresentEvaluator>();
builder.Services.AddSingleton<IResponseEvaluator, BdToolOrderingEvaluator>();
builder.Services.AddSingleton<AgentService>();
builder.Services.AddSingleton<SuggestionService>();
builder.Services.AddSingleton<ConversationService>();

// ── Background services ───────────────────────────────────────────────────────
builder.Services.AddHostedService<KafkaConsumerService>();
builder.Services.AddHostedService<SuggestionPoolMaintenanceService>();

// ── JSON snake_case globally ──────────────────────────────────────────────────
builder.Services.ConfigureHttpJsonOptions(opts =>
{
    opts.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower;
});

var app = builder.Build();

// ── Startup probes ────────────────────────────────────────────────────────────
var appState = app.Services.GetRequiredService<AppState>();
var startupLogger = app.Services.GetRequiredService<ILogger<Program>>();
var conversationService = app.Services.GetRequiredService<ConversationService>();

try { await conversationService.InitializeAsync(); }
catch (Exception ex) { startupLogger.LogWarning("Conversation DB init failed: {Error}", ex.Message); }

var availableModels = availableModelsRaw
    .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
    .ToList();
if (availableModels.Count == 0) availableModels.Add("gpt-4.1-mini");
appState.AvailableModels.AddRange(availableModels);

// MCP already connected at startup (above); Azure OpenAI client construction
// is synchronous and pre-flighted by DI. Mark both ready.
appState.McpConnected = true;
appState.LlmConnected = true;

// ── Endpoints ─────────────────────────────────────────────────────────────────

app.MapPost("/query", async (
    QueryRequest body,
    HttpContext httpContext,
    AgentService agentService,
    MemoryService memoryService,
    ConversationService conversationSvc,
    AppState state) =>
{
    if (!state.McpConnected || !state.LlmConnected)
        return Results.Problem(detail: "Agent not ready", statusCode: 503);

    var headerSessionId = httpContext.Request.Headers["X-Session-ID"].FirstOrDefault();
    var conversationId = httpContext.Request.Headers["X-Conversation-ID"].FirstOrDefault();
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    var sessionId = body.SessionId ?? headerSessionId ?? Guid.NewGuid().ToString();

    string deployment;
    if (!string.IsNullOrWhiteSpace(body.Model) && state.AvailableModels.Contains(body.Model))
    {
        deployment = body.Model;
    }
    else
    {
        var sessionModel = await memoryService.GetSessionModelAsync(sessionId);
        deployment = state.AvailableModels.Contains(sessionModel) ? sessionModel : state.DefaultModel;
    }

    AgentResult result;
    try
    {
        // Key the agent session by conversationId when present (so URL-shared
        // links resume the same conversation); fall back to sessionId otherwise.
        var agentSessionKey = !string.IsNullOrWhiteSpace(conversationId)
            ? conversationId
            : sessionId;
        result = await agentService.RunAgentAsync(
            query: body.Query,
            sessionId: agentSessionKey,
            deployment: deployment,
            ct: httpContext.RequestAborted);
    }
    catch (Exception ex)
    {
        var errTraceId = GetDdTraceId(httpContext, Activity.Current);
        return Results.Problem(detail: ex.Message, statusCode: 500,
            extensions: new Dictionary<string, object?> { ["trace_id"] = errTraceId });
    }

    await memoryService.SetSessionModelAsync(sessionId, deployment);

    var traceId = GetDdTraceId(httpContext, Activity.Current);
    var spanId = GetDdSpanId(Activity.Current);

    if (!string.IsNullOrWhiteSpace(conversationId) && !string.IsNullOrWhiteSpace(userId))
    {
        await conversationSvc.SaveMessagesAsync(
            conversationId, body.Query, result.Answer,
            result.Sources, traceId, spanId);
    }

    return Results.Ok(new QueryResponse(
        Answer: result.Answer,
        Sources: result.Sources,
        TraceId: traceId,
        SpanId: spanId,
        SessionId: sessionId,
        Model: deployment));
});

app.MapPost("/suggestions", async (
    SuggestionsRequest body,
    SuggestionService suggestionService,
    AppState state) =>
{
    if (!state.LlmConnected)
        return Results.Ok(new SuggestionsResponse(SuggestionService.FallbackSuggestions));

    var suggestions = await suggestionService.GetContextualSuggestionsAsync(
        body.Query, body.Answer, body.Sources ?? new List<string>());
    return Results.Ok(new SuggestionsResponse(suggestions));
});

app.MapGet("/suggestions/initial", async (
    SuggestionService suggestionService,
    AppState state) =>
{
    var picked = await suggestionService.GetRandomFromPoolAsync(4);
    if (picked.Count > 0)
    {
        var poolSize = await suggestionService.GetPoolSizeAsync();
        if (poolSize < 20 && state.LlmConnected)
            _ = Task.Run(() => suggestionService.FillPoolAsync());
        return Results.Ok(new SuggestionsResponse(picked));
    }

    if (!state.LlmConnected)
        return Results.Ok(new SuggestionsResponse(SuggestionService.FallbackSuggestions));

    try
    {
        await suggestionService.FillPoolAsync();
        var fresh = await suggestionService.GetRandomFromPoolAsync(4);
        if (fresh.Count > 0) return Results.Ok(new SuggestionsResponse(fresh));
    }
    catch (Exception ex)
    {
        app.Logger.LogWarning("Initial suggestions fallback LLM call failed: {Error}", ex.Message);
    }

    return Results.Ok(new SuggestionsResponse(SuggestionService.FallbackSuggestions));
});

app.MapGet("/models", (AppState state) =>
    Results.Ok(new { models = state.AvailableModels, @default = state.DefaultModel }));

app.MapGet("/tools", async (McpClient mcp, AppState state, HttpContext httpContext) =>
{
    if (!state.McpConnected)
        return Results.Problem(detail: "MCP client not available", statusCode: 503);

    var tools = await mcp.ListToolsAsync(cancellationToken: httpContext.RequestAborted);
    var result = tools.Select(t => new
    {
        name = t.Name,
        description = t.Description,
    });
    return Results.Ok(result);
});

app.MapPost("/feedback", (FeedbackRequest body) =>
{
    var validRatings = new HashSet<string> { "positive", "negative", "reported" };
    if (!validRatings.Contains(body.Rating))
    {
        return Results.Problem(
            detail: $"Invalid rating '{body.Rating}'. Must be one of: {string.Join(", ", validRatings.Order())}",
            statusCode: 422);
    }

    // Feedback now flows as a tag on the current trace's HTTP span — the
    // hand-rolled "user-feedback" activity from the old LlmTelemetry helper
    // is gone. APM picks it up via the AspNetCore instrumentation.
    var current = Activity.Current;
    current?.SetTag("feedback.trace_id", body.TraceId);
    current?.SetTag("feedback.span_id", body.SpanId);
    current?.SetTag("feedback.rating", body.Rating);
    current?.SetTag("feedback.session_id", body.SessionId ?? "");

    feedbackCounter.Add(1, new KeyValuePair<string, object?>("rating", body.Rating));

    return Results.StatusCode(204);
});

app.MapGet("/health", (AppState state) =>
    Results.Ok(new
    {
        status = "ok",
        service = "infra-advisor-agent-api-dotnet",
        mcp_connected = state.McpConnected,
        llm_connected = state.LlmConnected,
    }));

app.MapDelete("/session/{sessionId}", async (string sessionId, MemoryService memoryService) =>
{
    var cleared = await memoryService.ClearSessionAsync(sessionId);
    return Results.Ok(new { session_id = sessionId, cleared = cleared });
});

// ── Conversations ─────────────────────────────────────────────────────────────

app.MapPost("/conversations", async (HttpContext httpContext, ConversationService conversationSvc) =>
{
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    if (string.IsNullOrWhiteSpace(userId))
        return Results.Problem(detail: "X-User-ID header required", statusCode: 400);

    string? title = null, model = null, backend = null;
    try
    {
        using var doc = await JsonDocument.ParseAsync(httpContext.Request.Body, cancellationToken: httpContext.RequestAborted);
        if (doc.RootElement.TryGetProperty("title", out var t)) title = t.GetString();
        if (doc.RootElement.TryGetProperty("model", out var m)) model = m.GetString();
        if (doc.RootElement.TryGetProperty("backend", out var b)) backend = b.GetString();
    }
    catch { }

    var conv = await conversationSvc.CreateConversationAsync(
        userId, title ?? "New Conversation", model, backend ?? "dotnet");
    return conv is null
        ? Results.Problem(detail: "Conversation persistence not available", statusCode: 503)
        : Results.Ok(conv);
});

app.MapGet("/conversations", async (HttpContext httpContext, ConversationService conversationSvc) =>
{
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    if (string.IsNullOrWhiteSpace(userId))
        return Results.Problem(detail: "X-User-ID header required", statusCode: 400);
    var list = await conversationSvc.ListConversationsAsync(userId);
    return Results.Ok(list);
});

app.MapGet("/conversations/{id}", async (string id, HttpContext httpContext, ConversationService conversationSvc) =>
{
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    if (string.IsNullOrWhiteSpace(userId))
        return Results.Problem(detail: "X-User-ID header required", statusCode: 400);
    var conv = await conversationSvc.GetConversationAsync(id, userId);
    return conv is null ? Results.NotFound() : Results.Ok(conv);
});

app.MapDelete("/conversations/{id}", async (string id, HttpContext httpContext, ConversationService conversationSvc) =>
{
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    if (string.IsNullOrWhiteSpace(userId))
        return Results.Problem(detail: "X-User-ID header required", statusCode: 400);
    var deleted = await conversationSvc.DeleteConversationAsync(id, userId);
    return deleted ? Results.StatusCode(204) : Results.NotFound();
});

app.Run();
