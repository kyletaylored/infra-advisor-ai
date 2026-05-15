using InfraAdvisor.McpServer.Observability;
using InfraAdvisor.McpServer.Tools;

var builder = WebApplication.CreateBuilder(args);

// ── OpenTelemetry + Logging ───────────────────────────────────────────────────
TelemetrySetup.Configure(builder);

// ── HttpClient factory ────────────────────────────────────────────────────────
builder.Services.AddHttpClient();

// ── MCP Server ────────────────────────────────────────────────────────────────
// Stateless = true makes each MCP HTTP request independent (no Mcp-Session-Id
// tracking server-side). Required for our multi-replica deployment: the K8s
// Service round-robins requests, so a session created on pod A and a follow-up
// on pod B would 404 without this. Trade-off: server cannot push unsolicited
// messages and the /sse endpoint is disabled — fine, we use request/response
// JSON-RPC only.
builder.Services.AddMcpServer()
    .WithHttpTransport(options => options.Stateless = true)
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
        tavily = !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("TAVILY_API_KEY")),
    },
}));

app.Run();
