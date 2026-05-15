using InfraAdvisor.McpServer.Observability;
using InfraAdvisor.McpServer.Tools;

var builder = WebApplication.CreateBuilder(args);

// ── OpenTelemetry + Logging ───────────────────────────────────────────────────
TelemetrySetup.Configure(builder);

// ── HttpClient factory ────────────────────────────────────────────────────────
builder.Services.AddHttpClient();

// ── MCP Server ────────────────────────────────────────────────────────────────
// Session-stateful HTTP transport (the SDK default). Tested Stateless=true
// previously to avoid Mcp-Session-Id 404s across multi-replica load balancing,
// but the .NET MCP client library still issues session-aware requests (assumes
// prior initialize) — stateless servers reject them with 400 JsonRpcError.
// Real fix lives in the K8s Service via sessionAffinity:ClientIP so the
// agent-api pod always lands on the same server pod for the duration of its
// session.
builder.Services.AddMcpServer()
    .WithHttpTransport()
    .WithTools<BridgeConditionTool>()
    .WithTools<DisasterHistoryTool>()
    .WithTools<EnergyInfrastructureTool>()
    .WithTools<WaterInfrastructureTool>()
    .WithTools<ProjectKnowledgeTool>()
    .WithTools<DraftDocumentTool>()
    .WithTools<ErcotEnergyTool>()
    .WithTools<TxDotOpenDataTool>()
    .WithTools<ProcurementOpportunitiesTool>()
    .WithTools<ContractAwardsTool>()
    .WithTools<WebProcurementSearchTool>();

var app = builder.Build();

// ── MCP transport (stateless HTTP) ────────────────────────────────────────────
app.MapMcp("/mcp");

// ── Health endpoint ───────────────────────────────────────────────────────────
app.MapGet("/health", () => Results.Ok(new
{
    status = "ok",
    service = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME") ?? "infra-advisor-mcp-server-dotnet",
    tools = new[]
    {
        "get_bridge_condition",
        "get_disaster_history",
        "get_energy_infrastructure",
        "get_water_infrastructure",
        "get_ercot_energy_storage",
        "search_txdot_open_data",
        "search_project_knowledge",
        "draft_document",
        "get_procurement_opportunities",
        "get_contract_awards",
        "search_web_procurement",
    },
    keys_configured = new
    {
        samgov = !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("SAMGOV_API_KEY")),
        // Web search now uses Azure OpenAI's web_search_preview tool — same
        // AZURE_OPENAI_API_KEY/ENDPOINT as the rest of the integration. No
        // separate third-party key required.
        azure_openai = !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY")),
    },
}));

app.Run();
