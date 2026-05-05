using System.Diagnostics;
using System.Text.Json;
using Azure.AI.OpenAI;
using InfraAdvisor.AgentApi.Models;
using InfraAdvisor.AgentApi.Observability;
using InfraAdvisor.AgentApi.Services;
using OpenAI.Chat;
using StackExchange.Redis;

var builder = WebApplication.CreateBuilder(args);

// ── Environment variable helpers ──────────────────────────────────────────────
static string Env(string key, string? fallback = null) =>
    Environment.GetEnvironmentVariable(key)
    ?? fallback
    ?? throw new InvalidOperationException($"Required environment variable '{key}' is not set.");

static string EnvOr(string key, string fallback) =>
    Environment.GetEnvironmentVariable(key) ?? fallback;

// ── Read configuration ────────────────────────────────────────────────────────
var azureEndpoint = Env("AZURE_OPENAI_ENDPOINT");
var azureApiKey = Env("AZURE_OPENAI_API_KEY");
var azureDeployment = EnvOr("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini");
var availableModelsRaw = EnvOr("AVAILABLE_MODELS", "gpt-4.1-mini");
var mcpServerUrl = EnvOr("MCP_SERVER_URL", "http://mcp-server.infra-advisor.svc.cluster.local:8000/mcp");
var redisHost = EnvOr("REDIS_HOST", "redis.infra-advisor.svc.cluster.local");
var redisPort = int.Parse(EnvOr("REDIS_PORT", "6379"));
var kafkaBootstrapServers = EnvOr("KAFKA_BOOTSTRAP_SERVERS", "kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092");

// Pass through to env for services that read directly
Environment.SetEnvironmentVariable("AZURE_OPENAI_ENDPOINT", azureEndpoint);
Environment.SetEnvironmentVariable("AZURE_OPENAI_API_KEY", azureApiKey);
Environment.SetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT", azureDeployment);
Environment.SetEnvironmentVariable("KAFKA_BOOTSTRAP_SERVERS", kafkaBootstrapServers);

// ── OpenTelemetry ─────────────────────────────────────────────────────────────
TelemetrySetup.Configure(builder.Services);

// ── ActivitySource (custom spans) ─────────────────────────────────────────────
builder.Services.AddSingleton(new ActivitySource(TelemetrySetup.ActivitySourceName));

// ── AppState singleton ────────────────────────────────────────────────────────
builder.Services.AddSingleton(new AppState());

// ── Redis ─────────────────────────────────────────────────────────────────────
builder.Services.AddSingleton<IConnectionMultiplexer>(_ =>
{
    try
    {
        var cfg = new ConfigurationOptions
        {
            EndPoints = { $"{redisHost}:{redisPort}" },
            AbortOnConnectFail = false,
            ConnectTimeout = 5000,
            SyncTimeout = 5000,
        };
        return ConnectionMultiplexer.Connect(cfg);
    }
    catch (Exception ex)
    {
        // Return a dummy multiplexer that will fail gracefully per-call
        var loggerFactory = LoggerFactory.Create(b => b.AddConsole());
        loggerFactory.CreateLogger("Redis").LogWarning("Redis connection failed: {Error}", ex.Message);
        // Still return a multiplexer configured to not abort — MemoryService handles failures gracefully
        var cfg = new ConfigurationOptions
        {
            EndPoints = { $"{redisHost}:{redisPort}" },
            AbortOnConnectFail = false,
        };
        return ConnectionMultiplexer.Connect(cfg);
    }
});

// ── MCP client ────────────────────────────────────────────────────────────────
builder.Services.AddHttpClient<McpClientService>(client =>
{
    client.BaseAddress = new Uri(mcpServerUrl);
    client.Timeout = TimeSpan.FromSeconds(30);
});

// ── Core services ─────────────────────────────────────────────────────────────
builder.Services.AddSingleton<MemoryService>();
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

// ── Logging ───────────────────────────────────────────────────────────────────
builder.Logging.ClearProviders();
builder.Logging.AddConsole(opts =>
{
    opts.FormatterName = "simple";
});

var app = builder.Build();

// ── Connectivity probe on startup ─────────────────────────────────────────────
var appState = app.Services.GetRequiredService<AppState>();
var mcpClient = app.Services.GetRequiredService<McpClientService>();
var startupLogger = app.Services.GetRequiredService<ILogger<Program>>();
var conversationService = app.Services.GetRequiredService<ConversationService>();

// Init DB schema (non-fatal — no DATABASE_URL means persistence is simply disabled)
try { await conversationService.InitializeAsync(); }
catch (Exception ex) { startupLogger.LogWarning("Conversation DB init failed: {Error}", ex.Message); }

// Parse available models
var availableModels = availableModelsRaw
    .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
    .ToList();
if (availableModels.Count == 0) availableModels.Add("gpt-4.1-mini");
appState.AvailableModels.AddRange(availableModels);

app.Lifetime.ApplicationStarted.Register(() =>
{
    _ = Task.Run(async () =>
    {
        // Probe MCP
        try
        {
            await mcpClient.ListToolsAsync();
            appState.McpConnected = true;
            startupLogger.LogInformation("MCP client connected to {Url}", mcpServerUrl);
        }
        catch (Exception ex)
        {
            startupLogger.LogWarning("MCP client failed to connect (will retry per-request): {Error}", ex.Message);
            appState.McpConnected = false;
        }

        // Probe LLM
        try
        {
            var azure = app.Services.GetRequiredService<AgentService>().AzureClient;
            var chat = azure.GetChatClient(azureDeployment);
            var probe = await chat.CompleteChatAsync(
                new List<ChatMessage> { new UserChatMessage("ping") },
                new ChatCompletionOptions { MaxOutputTokenCount = 1 });
            appState.LlmConnected = true;
            startupLogger.LogInformation("LLM client connected (deployment={Deployment})", azureDeployment);
        }
        catch (Exception ex)
        {
            startupLogger.LogWarning("LLM client probe failed: {Error}", ex.Message);
            appState.LlmConnected = false;
        }
    });
});

// ── Endpoints ─────────────────────────────────────────────────────────────────

// POST /query
app.MapPost("/query", async (
    QueryRequest body,
    HttpContext httpContext,
    AgentService agentService,
    MemoryService memoryService,
    ConversationService conversationSvc,
    AppState state,
    ActivitySource activitySource) =>
{
    if (!state.McpConnected || !state.LlmConnected)
    {
        return Results.Problem(
            detail: "Agent not ready — MCP or LLM client unavailable",
            statusCode: 503);
    }

    var headerSessionId = httpContext.Request.Headers["X-Session-ID"].FirstOrDefault();
    var rumSessionId = httpContext.Request.Headers["X-DD-RUM-Session-ID"].FirstOrDefault();
    var conversationId = httpContext.Request.Headers["X-Conversation-ID"].FirstOrDefault();
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    var sessionId = headerSessionId ?? body.SessionId ?? Guid.NewGuid().ToString();

    // Resolve model: request body > session memory > default
    string deployment;
    if (!string.IsNullOrWhiteSpace(body.Model) && state.AvailableModels.Contains(body.Model))
    {
        deployment = body.Model;
    }
    else
    {
        var sessionModel = await memoryService.GetSessionModelAsync(sessionId);
        deployment = state.AvailableModels.Contains(sessionModel)
            ? sessionModel
            : state.DefaultModel;
    }

    AgentResult result;
    try
    {
        result = await agentService.RunAgentAsync(
            query: body.Query,
            sessionId: sessionId,
            deployment: deployment,
            rumSessionId: rumSessionId,
            ct: httpContext.RequestAborted);
    }
    catch (Exception ex)
    {
        var traceIdErr = Activity.Current?.TraceId.ToString();
        return Results.Problem(detail: ex.Message, statusCode: 500,
            extensions: new Dictionary<string, object?> { ["trace_id"] = traceIdErr });
    }

    await memoryService.AppendExchangeAsync(sessionId, body.Query, result.Answer);
    await memoryService.SetSessionModelAsync(sessionId, deployment);

    var traceId = Activity.Current?.TraceId.ToString();
    var spanId = Activity.Current?.SpanId.ToString();

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
        Model: deployment
    ));
});

// POST /suggestions
app.MapPost("/suggestions", async (
    SuggestionsRequest body,
    SuggestionService suggestionService,
    AgentService agentService,
    AppState state) =>
{
    if (!state.LlmConnected)
        return Results.Ok(new SuggestionsResponse(SuggestionService.FallbackSuggestions));

    var chatClient = agentService.AzureClient.GetChatClient(agentService.DefaultDeployment);
    var suggestions = await suggestionService.GetContextualSuggestionsAsync(
        body.Query, body.Answer, body.Sources ?? new List<string>(), chatClient);

    return Results.Ok(new SuggestionsResponse(suggestions));
});

// GET /suggestions/initial
app.MapGet("/suggestions/initial", async (
    SuggestionService suggestionService,
    AgentService agentService,
    AppState state) =>
{
    var picked = await suggestionService.GetRandomFromPoolAsync(4);
    if (picked.Count > 0)
    {
        // Async top-up if pool is running low — doesn't block the response
        var poolSize = await suggestionService.GetPoolSizeAsync();
        if (poolSize < 20 && state.LlmConnected)
        {
            var chatClient = agentService.AzureClient.GetChatClient(agentService.DefaultDeployment);
            _ = Task.Run(() => suggestionService.FillPoolAsync(chatClient));
        }
        return Results.Ok(new SuggestionsResponse(picked));
    }

    // Pool empty — call LLM directly and seed pool
    if (!state.LlmConnected)
        return Results.Ok(new SuggestionsResponse(SuggestionService.FallbackSuggestions));

    try
    {
        var chatClient = agentService.AzureClient.GetChatClient(agentService.DefaultDeployment);
        await suggestionService.FillPoolAsync(chatClient);
        var fresh = await suggestionService.GetRandomFromPoolAsync(4);
        if (fresh.Count > 0)
            return Results.Ok(new SuggestionsResponse(fresh));
    }
    catch (Exception ex)
    {
        app.Logger.LogWarning("Initial suggestions fallback LLM call failed: {Error}", ex.Message);
    }

    return Results.Ok(new SuggestionsResponse(SuggestionService.FallbackSuggestions));
});

// GET /models
app.MapGet("/models", (AppState state) =>
    Results.Ok(new
    {
        models = state.AvailableModels,
        @default = state.DefaultModel,
    }));

// GET /tools
app.MapGet("/tools", async (McpClientService mcp, AppState state, HttpContext httpContext) =>
{
    if (!state.McpConnected)
        return Results.Problem(detail: "MCP client not available", statusCode: 503);

    var tools = await mcp.ListToolsAsync(httpContext.RequestAborted);
    var result = tools.Select(t => new
    {
        name = t.Name,
        description = t.Description,
        parameters = t.InputSchema.ValueKind != System.Text.Json.JsonValueKind.Undefined
            ? (object)t.InputSchema
            : new { type = "object", properties = new { } },
    });
    return Results.Ok(result);
});

// POST /tools/{toolName}
app.MapPost("/tools/{toolName}", async (
    string toolName,
    HttpContext httpContext,
    McpClientService mcp,
    AppState state) =>
{
    if (!state.McpConnected)
        return Results.Problem(detail: "MCP client not available", statusCode: 503);

    JsonElement args = default;
    try
    {
        using var doc = await JsonDocument.ParseAsync(httpContext.Request.Body, cancellationToken: httpContext.RequestAborted);
        args = doc.RootElement.Clone();
    }
    catch
    {
        args = JsonDocument.Parse("{}").RootElement;
    }

    var sw = Stopwatch.StartNew();
    try
    {
        var result = await mcp.InvokeToolAsync(toolName, args, httpContext.RequestAborted);
        sw.Stop();
        return Results.Ok(new
        {
            tool_name = toolName,
            result = result,
            duration_ms = Math.Round(sw.Elapsed.TotalMilliseconds, 2),
        });
    }
    catch (Exception ex)
    {
        sw.Stop();
        return Results.Ok(new
        {
            tool_name = toolName,
            error = ex.Message,
            duration_ms = Math.Round(sw.Elapsed.TotalMilliseconds, 2),
        });
    }
});

// POST /feedback
app.MapPost("/feedback", (
    FeedbackRequest body,
    ActivitySource activitySource) =>
{
    var validRatings = new HashSet<string> { "positive", "negative", "reported" };
    if (!validRatings.Contains(body.Rating))
    {
        return Results.Problem(
            detail: $"Invalid rating '{body.Rating}'. Must be one of: {string.Join(", ", validRatings.Order())}",
            statusCode: 422);
    }

    using var feedbackActivity = activitySource.StartActivity("user-feedback");
    feedbackActivity?.SetTag("feedback.trace_id", body.TraceId);
    feedbackActivity?.SetTag("feedback.span_id", body.SpanId);
    feedbackActivity?.SetTag("feedback.rating", body.Rating);
    feedbackActivity?.SetTag("session.id", body.SessionId ?? "");

    return Results.StatusCode(204);
});

// GET /health
app.MapGet("/health", (AppState state) =>
    Results.Ok(new
    {
        status = "ok",
        service = "infraadvisor-agent-api-dotnet",
        mcp_connected = state.McpConnected,
        llm_connected = state.LlmConnected,
    }));

// DELETE /session/{sessionId}
app.MapDelete("/session/{sessionId}", async (string sessionId, MemoryService memoryService) =>
{
    var cleared = await memoryService.ClearSessionAsync(sessionId);
    return Results.Ok(new { session_id = sessionId, cleared = cleared });
});

// POST /conversations
app.MapPost("/conversations", async (
    HttpContext httpContext,
    ConversationService conversationSvc) =>
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
    catch { /* body optional */ }

    var conv = await conversationSvc.CreateConversationAsync(
        userId, title ?? "New Conversation", model, backend ?? "dotnet");
    return conv is null
        ? Results.Problem(detail: "Conversation persistence not available", statusCode: 503)
        : Results.Ok(conv);
});

// GET /conversations
app.MapGet("/conversations", async (
    HttpContext httpContext,
    ConversationService conversationSvc) =>
{
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    if (string.IsNullOrWhiteSpace(userId))
        return Results.Problem(detail: "X-User-ID header required", statusCode: 400);

    var list = await conversationSvc.ListConversationsAsync(userId);
    return Results.Ok(list);
});

// GET /conversations/{id}
app.MapGet("/conversations/{id}", async (
    string id,
    HttpContext httpContext,
    ConversationService conversationSvc) =>
{
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    if (string.IsNullOrWhiteSpace(userId))
        return Results.Problem(detail: "X-User-ID header required", statusCode: 400);

    var conv = await conversationSvc.GetConversationAsync(id, userId);
    return conv is null ? Results.NotFound() : Results.Ok(conv);
});

// DELETE /conversations/{id}
app.MapDelete("/conversations/{id}", async (
    string id,
    HttpContext httpContext,
    ConversationService conversationSvc) =>
{
    var userId = httpContext.Request.Headers["X-User-ID"].FirstOrDefault();
    if (string.IsNullOrWhiteSpace(userId))
        return Results.Problem(detail: "X-User-ID header required", statusCode: 400);

    var deleted = await conversationSvc.DeleteConversationAsync(id, userId);
    return deleted ? Results.StatusCode(204) : Results.NotFound();
});

app.Run();
