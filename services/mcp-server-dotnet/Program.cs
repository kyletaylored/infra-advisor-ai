using InfraAdvisor.McpServer.Observability;
using InfraAdvisor.McpServer.Tools;

var builder = WebApplication.CreateBuilder(args);

// ── OpenTelemetry ─────────────────────────────────────────────────────────────
TelemetrySetup.Configure(builder.Services);

// ── Logging ───────────────────────────────────────────────────────────────────
builder.Logging.ClearProviders();
builder.Logging.AddConsole();

// ── HttpClient factory ────────────────────────────────────────────────────────
builder.Services.AddHttpClient();

// ── MCP Server ────────────────────────────────────────────────────────────────
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
    service = Environment.GetEnvironmentVariable("DD_SERVICE") ?? "infratools-mcp-dotnet",
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
