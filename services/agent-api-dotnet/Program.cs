using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading.RateLimiting;
using Azure;
using Azure.AI.OpenAI;
using InfraAdvisor.AgentApi.Models;
using InfraAdvisor.AgentApi.Observability;
using InfraAdvisor.AgentApi.Services;
using InfraAdvisor.AgentApi.Services.Evaluators;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.Extensions.AI;
using Microsoft.IdentityModel.Tokens;
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
var redisPassword = Environment.GetEnvironmentVariable("REDIS_PASSWORD");
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
        Password = redisPassword,
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

// ── MCP client holder — lazy connect with reconnect-on-session-expired ─────
// Previously we did a synchronous McpClient.CreateAsync at startup and
// registered the resulting client as a singleton. That worked fine until
// mcp-server-dotnet restarted (any rollout, OOM, AKS rebalance) — the
// cached client's session ID stopped resolving on the new server pod and
// every tool call returned HTTP 404. The only mitigation was to manually
// `kubectl rollout restart deployment/agent-api-dotnet`.
//
// McpClientHolder fixes that: it lazy-connects on first use, exposes
// RefreshAsync() to recreate the client + tool list on demand, and
// returns a monotonically-incrementing Generation that the AgentHolder
// uses as a cache key. AgentService catches session-expired exceptions
// and calls RefreshAsync — first request after an mcp-server restart
// pays one extra round trip; everything after is normal.
builder.Services.AddSingleton(sp => new McpClientHolder(
    serverUrl: mcpServerUrl,
    clientName: "infra-advisor-agent-api-dotnet",
    logger: sp.GetRequiredService<ILogger<McpClientHolder>>()));

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
    "detailed for engineering analysis and document drafts.\n\n" +
    // ── Few-shot tool-call examples ─────────────────────────────────────────────
    // Concrete worked patterns the model can anchor on for the high-error
    // decision points: FIPS state codes (not 2-letter abbrevs), AEC NAICS
    // codes (not category names), the BD chain pattern, water query_type
    // dispatch, the document-drafting chain. Keeping these tight — verbosity
    // here costs every request's input tokens.
    "Examples of correct tool calls:\n\n" +

    "User: \"Worst-rated bridges in California\"\n" +
    "→ get_bridge_condition(state_code=\"06\", max_lowest_rating=4, limit=25)\n" +
    "  (Note: state_code is 2-char FIPS with leading zero. CA=06, TX=48, FL=12, NY=36.)\n\n" +

    "User: \"Find recent federal highway construction awards in Texas under NAICS 237310, " +
    "then list open opportunities matching the same NAICS\"\n" +
    "→ get_contract_awards(query=\"highway construction\", geography=\"TX\", naics_codes=[\"237310\"])\n" +
    "→ get_procurement_opportunities(query=\"highway construction\", geography=\"TX\", naics_codes=[\"237310\"])\n" +
    "  (BD pairing rule: awards FIRST so competitive context informs the open-opportunity " +
    "list. Never ask the user for a date range.)\n\n" +

    "User: \"Which Texas community water systems have SDWA violations serving 10K+ people?\"\n" +
    "→ get_water_infrastructure(query_type=\"violations\", states=[\"TX\"], " +
    "system_types=[\"CWS\"], has_violations=true, min_population_served=10000)\n" +
    "  (query_type=\"violations\" — not \"water_systems\". CWS = Community Water System.)\n\n" +

    "User: \"Draft an SOW for an IH-35 bridge rehabilitation project\"\n" +
    "→ search_project_knowledge(query=\"bridge rehabilitation SOW IH-35\", " +
    "document_types=[\"sow\", \"case_study\"])\n" +
    "→ draft_document(document_type=\"scope_of_work\", context={...retrieved snippets...}, " +
    "project_name=\"IH-35 Bridge Rehabilitation\")\n" +
    "  (ALWAYS call search_project_knowledge first to pull templates + prior project " +
    "context; pass retrieved content into context for draft_document.)\n\n" +

    "User: \"Texas renewable energy generation share over the last 5 years\"\n" +
    "→ get_energy_infrastructure(states=[\"TX\"], data_series=\"fuel_mix\", " +
    "year_from=2019, year_to=2024)\n" +
    "  (data_series=\"fuel_mix\" returns % share by fuel — what \"renewable share\" means. " +
    "Use \"generation\" for raw MWh, \"capacity\" for installed MW.)";

// AgentHolder builds (and rebuilds) the ChatClientAgent against the current
// McpClientHolder tool list. ChatClientAgent's ChatOptions.Tools is captured
// at construction, so the agent must be rebuilt after each MCP reconnect to
// pick up the fresh AITool instances. Holder caches against
// McpClientHolder.Generation — one rebuild per reconnect, not per request.
builder.Services.AddSingleton(sp => new AgentHolder(
    chatClient:     sp.GetRequiredService<IChatClient>(),
    mcpHolder:      sp.GetRequiredService<McpClientHolder>(),
    systemPrompt:   AgentSystemPrompt,
    agentName:      "infra-advisor",
    otelSourceName: TelemetrySetup.ActivitySourceName));

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
// In-memory ring buffer of recent eval-submission outcomes, surfaced by the
// admin diagnostics panel via GET /eval/status. Single instance shared with
// every DatadogEvalsClient submission so the panel can see the most recent
// 50 attempts across all evaluators.
builder.Services.AddSingleton<EvalSubmissionLog>();
builder.Services.AddHttpClient<DatadogEvalsClient>();
builder.Services.AddSingleton<IResponseEvaluator, CitationPresentEvaluator>();
builder.Services.AddSingleton<IResponseEvaluator, BdToolOrderingEvaluator>();
builder.Services.AddSingleton<IResponseEvaluator, ToolRoutingAccuracyEvaluator>();
// LLM-as-judge — wrappers around Microsoft.Extensions.AI.Evaluation.Quality.
// Uses the same IChatClient as the agent (gpt-4.1-mini) for the judge call;
// per-eval cost is one extra inference call on each sampled trace.
builder.Services.AddSingleton<IResponseEvaluator, MeaiRelevanceEvaluator>();
builder.Services.AddSingleton<IResponseEvaluator, MeaiGroundednessEvaluator>();
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

// ── JWT auth (shared secret with auth-api) ────────────────────────────────────
// Same JWT_SECRET / HS256 algorithm as services/auth-api/src/auth.py.
// Tokens issued by /auth/login validate here without a round-trip.
// Fails closed at startup if JWT_SECRET isn't set — better than running
// with an empty key and silently accepting forged tokens.
var jwtSecret = Environment.GetEnvironmentVariable("JWT_SECRET")
    ?? throw new InvalidOperationException(
        "JWT_SECRET env var is required — share the value with auth-api.");

builder.Services.AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
    .AddJwtBearer(opts =>
    {
        opts.RequireHttpsMetadata = false;  // TLS handled at the nginx ingress
        opts.SaveToken = false;
        opts.MapInboundClaims = false;      // keep JWT claim names as-is ("sub" stays "sub")
        opts.TokenValidationParameters = new TokenValidationParameters
        {
            ValidateIssuer = false,
            ValidateAudience = false,
            ValidateLifetime = true,
            ValidateIssuerSigningKey = true,
            IssuerSigningKey = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(jwtSecret)),
            ClockSkew = TimeSpan.FromMinutes(1),
        };
    });
builder.Services.AddAuthorization();

// ── Rate limiting ─────────────────────────────────────────────────────────────
// Per-user (or per-IP for unauthenticated) sliding window on /query and
// /query/stream. Keyed by JWT `sub` claim when available — same logic the
// Python service uses, so a user can't multiply their quota by switching IPs.
builder.Services.AddRateLimiter(opts =>
{
    opts.RejectionStatusCode = 429;
    opts.AddPolicy("query", httpContext =>
    {
        var key = httpContext.User?.FindFirst("sub")?.Value
                  ?? httpContext.Connection.RemoteIpAddress?.ToString()
                  ?? "anon";
        return RateLimitPartition.GetSlidingWindowLimiter(
            partitionKey: key,
            factory: _ => new SlidingWindowRateLimiterOptions
            {
                PermitLimit = 20,
                Window = TimeSpan.FromMinutes(1),
                SegmentsPerWindow = 4,
                QueueProcessingOrder = QueueProcessingOrder.OldestFirst,
                QueueLimit = 0,
            });
    });
});

var app = builder.Build();
app.UseAuthentication();
app.UseAuthorization();
app.UseRateLimiter();

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

// MCP connects lazily on the first /query (via McpClientHolder). We mark
// "connected" optimistically here so the /query gate doesn't reject the
// very first call before the holder has run its connect — if the holder
// can't reach mcp-server-dotnet it surfaces a clear exception inside the
// handler. Azure OpenAI client construction is synchronous and pre-
// flighted by DI.
appState.McpConnected = true;
appState.LlmConnected = true;

// Pre-warm the MCP connection in the background so the first /query
// doesn't pay the connect latency. Best-effort; failure is logged and
// the next /query will retry.
_ = Task.Run(async () =>
{
    try
    {
        var holder = app.Services.GetRequiredService<McpClientHolder>();
        await holder.GetClientAsync(CancellationToken.None);
    }
    catch (Exception ex)
    {
        startupLogger.LogWarning(
            "MCP pre-warm failed (will retry on first /query): {Error}", ex.Message);
    }
});

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
}).RequireAuthorization().RequireRateLimiting("query");

// ── /query/stream — Server-Sent Events streaming variant ──────────────────────
// Same agent pipeline as /query but yields one SSE block per StreamEvent so
// the UI can show classify_domain / retrieve_best_practices / tool_call /
// tool_call_end / text_chunk / done events live. NGINX in front of this
// pod must skip buffering on this path — set in services/ui/nginx.conf and
// reinforced by the X-Accel-Buffering: no response header below.
//
// Trade-off vs /query: no mid-stream MCP-session-expired retry (text we
// already streamed can't be cleanly rewound). Clients can fall back to
// /query if the streaming path fails; resilient reconnect lives there.
app.MapPost("/query/stream", async (
    QueryRequest body,
    HttpContext httpContext,
    AgentService agentService,
    MemoryService memoryService,
    ConversationService conversationSvc,
    AppState state) =>
{
    if (!state.McpConnected || !state.LlmConnected)
    {
        return Results.Problem(detail: "Agent not ready", statusCode: 503);
    }

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

    var agentSessionKey = !string.IsNullOrWhiteSpace(conversationId)
        ? conversationId
        : sessionId;

    httpContext.Response.Headers.ContentType = "text/event-stream";
    httpContext.Response.Headers.CacheControl = "no-cache";
    httpContext.Response.Headers.Append("X-Accel-Buffering", "no");
    httpContext.Response.Headers.Append("Connection", "keep-alive");

    var jsonOpts = new JsonSerializerOptions
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    // Buckets for the final conversation persistence — written once on
    // DoneEvent so the row in `messages` matches what /query would have
    // saved.
    var fullAnswer = new System.Text.StringBuilder();
    var doneSources = new List<string>();
    string? finalTraceId = null;
    string? finalSpanId = null;

    await foreach (var evt in agentService.RunAgentStreamingAsync(
        body.Query, agentSessionKey, deployment, httpContext.RequestAborted))
    {
        // Accumulate side-effects we need post-stream.
        switch (evt)
        {
            case TextChunkEvent t: fullAnswer.Append(t.Chunk); break;
            case DoneEvent d:
                doneSources.AddRange(d.Sources);
                finalTraceId = d.TraceId;
                finalSpanId = d.SpanId;
                break;
        }

        // Serialize without the EventName field (it goes on the SSE
        // "event:" line, not in the data payload).
        var payload = JsonSerializer.Serialize((object)evt, evt.GetType(), jsonOpts);
        await httpContext.Response.WriteAsync(
            $"event: {evt.EventName}\ndata: {payload}\n\n",
            httpContext.RequestAborted);
        await httpContext.Response.Body.FlushAsync(httpContext.RequestAborted);
    }

    await memoryService.SetSessionModelAsync(sessionId, deployment);

    if (!string.IsNullOrWhiteSpace(conversationId) && !string.IsNullOrWhiteSpace(userId))
    {
        await conversationSvc.SaveMessagesAsync(
            conversationId, body.Query, fullAnswer.ToString(),
            doneSources, finalTraceId, finalSpanId);
    }

    return Results.Empty;
}).RequireAuthorization().RequireRateLimiting("query");

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
}).RequireAuthorization();

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
}).RequireAuthorization();

app.MapGet("/models", (AppState state) =>
    Results.Ok(new { models = state.AvailableModels, @default = state.DefaultModel }));

app.MapGet("/tools", async (McpClientHolder holder, AppState state, HttpContext httpContext) =>
{
    if (!state.McpConnected)
        return Results.Problem(detail: "MCP client not available", statusCode: 503);

    var tools = await holder.GetToolsAsync(httpContext.RequestAborted);
    var result = tools.Select(t => new
    {
        name = t.Name,
        description = t.Description,
    });
    return Results.Ok(result);
}).RequireAuthorization();

// ── /eval/status — read-only diagnostics for the admin UI ─────────────────────
// Exposes the running eval pipeline state so admins can answer: "is the eval
// pipeline actually firing? at what sample rate? which evaluators are
// registered? are submissions reaching Datadog? what's the recent failure
// rate?" without grepping pod logs or hitting DD's UI.
//
// Read-only by design — mutating sample rate / toggling evaluators at runtime
// would require an audit story we haven't designed yet. See claude-progress.txt
// entry for the "Option A diagnostic panel" decision rationale.
app.MapGet("/eval/status", (
    IEnumerable<InfraAdvisor.AgentApi.Services.Evaluators.IResponseEvaluator> evaluators,
    DatadogEvalsClient ddEvals,
    EvalSubmissionLog log) =>
{
    var snapshot = log.Snapshot();
    var sampleRate = double.TryParse(
        Environment.GetEnvironmentVariable("EVAL_SAMPLE_RATE"),
        System.Globalization.NumberStyles.Float,
        System.Globalization.CultureInfo.InvariantCulture,
        out var r) ? Math.Clamp(r, 0.0, 1.0) : 0.1;

    return Results.Ok(new
    {
        sample_rate = sampleRate,
        eval_pipeline = new
        {
            registered_evaluators = evaluators.Select(e => new
            {
                label = e.Label,
                type_name = e.GetType().Name,
                is_llm_judge = e.GetType().Name.StartsWith("Meai", StringComparison.Ordinal),
            }).ToList(),
        },
        datadog = new
        {
            enabled = ddEvals.Enabled,
            ml_app = ddEvals.MlApp,
            site = ddEvals.Site,
            api_key_configured = ddEvals.Enabled,
        },
        judge = new
        {
            deployment = Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT") ?? "gpt-4.1-mini",
            note = "M.E.AI Quality evaluator prompts tuned best for GPT-4o-class models. " +
                   "Scores from this deployment are useful as trend signal; " +
                   "absolute thresholds need recalibration.",
        },
        submissions = new
        {
            total = snapshot.TotalSubmitted,
            failed = snapshot.TotalFailed,
            success_rate = snapshot.TotalSubmitted == 0
                ? (double?)null
                : Math.Round(1.0 - (double)snapshot.TotalFailed / snapshot.TotalSubmitted, 3),
            recent = snapshot.Recent.Select(e => new
            {
                timestamp_iso = e.Timestamp.ToString("o"),
                label = e.Label,
                metric_type = e.MetricType,
                value = e.Value,
                success = e.Success,
                duration_ms = e.DurationMs,
                trace_id_decimal = e.TraceIdDecimal,
                span_id_decimal = e.SpanIdDecimal,
                reasoning = e.Reasoning,
                error = e.Error,
            }).ToList(),
        },
    });
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
}).RequireAuthorization();

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
}).RequireAuthorization();

// ── Conversations ─────────────────────────────────────────────────────────────
// User identity is the JWT `sub` claim — the previous X-User-ID header was
// spoofable. UseAuthentication() above populates HttpContext.User; the
// RequireAuthorization() suffix below guarantees it's non-null.

static string? SubClaim(HttpContext ctx) =>
    ctx.User?.FindFirst("sub")?.Value;

app.MapPost("/conversations", async (HttpContext httpContext, ConversationService conversationSvc) =>
{
    var userId = SubClaim(httpContext);
    if (userId is null) return Results.Unauthorized();

    string? title = null, model = null, backend = null;
    try
    {
        using var doc = await JsonDocument.ParseAsync(httpContext.Request.Body, cancellationToken: httpContext.RequestAborted);
        if (doc.RootElement.TryGetProperty("title", out var t)) title = t.GetString();
        if (doc.RootElement.TryGetProperty("model", out var m)) model = m.GetString();
        if (doc.RootElement.TryGetProperty("backend", out var b)) backend = b.GetString();
    }
    catch { }

    try
    {
        var conv = await conversationSvc.CreateConversationAsync(
            userId, title ?? "New Conversation", model, backend ?? "dotnet");
        return conv is null
            ? Results.Problem(detail: "Conversation persistence not available", statusCode: 503)
            : Results.Ok(conv);
    }
    catch (Exception ex)
    {
        return Results.Problem(detail: ex.Message, statusCode: 500);
    }
}).RequireAuthorization();

app.MapGet("/conversations", async (HttpContext httpContext, ConversationService conversationSvc) =>
{
    var userId = SubClaim(httpContext);
    if (userId is null) return Results.Unauthorized();
    var list = await conversationSvc.ListConversationsAsync(userId);
    return Results.Ok(list);
}).RequireAuthorization();

app.MapGet("/conversations/{id}", async (string id, HttpContext httpContext, ConversationService conversationSvc) =>
{
    var userId = SubClaim(httpContext);
    if (userId is null) return Results.Unauthorized();
    var conv = await conversationSvc.GetConversationAsync(id, userId);
    return conv is null ? Results.NotFound() : Results.Ok(conv);
}).RequireAuthorization();

app.MapDelete("/conversations/{id}", async (string id, HttpContext httpContext, ConversationService conversationSvc) =>
{
    var userId = SubClaim(httpContext);
    if (userId is null) return Results.Unauthorized();
    var deleted = await conversationSvc.DeleteConversationAsync(id, userId);
    return deleted ? Results.StatusCode(204) : Results.NotFound();
}).RequireAuthorization();

app.Run();
