using System.ComponentModel;
using System.Reflection;
using System.Text.Json;
using ModelContextProtocol.Server;
using Scriban;
using Scriban.Runtime;

namespace InfraAdvisor.McpServer.Tools;

[McpServerToolType]
public sealed class DraftDocumentTool(ILogger<DraftDocumentTool> logger)
{
    private static readonly Dictionary<string, string> TemplateMap = new()
    {
        ["scope_of_work"] = "InfraAdvisor.McpServer.Templates.scope_of_work.md",
        ["risk_summary"] = "InfraAdvisor.McpServer.Templates.risk_summary.md",
        ["cost_estimate_scaffold"] = "InfraAdvisor.McpServer.Templates.cost_estimate_scaffold.md",
        ["funding_positioning_memo"] = "InfraAdvisor.McpServer.Templates.funding_positioning_memo.md",
    };

    [McpServerTool(Name = "draft_document")]
    [Description(
        "Render a structured consulting deliverable from a Scriban template + the " +
        "supplied context dictionary. Returns Markdown ready for client review. " +
        "DETERMINISTIC — no LLM invoked inside the tool.\n" +
        "ALWAYS call search_project_knowledge FIRST to pull relevant templates and " +
        "prior-project context, then pass the retrieved snippets into context here " +
        "so the draft is grounded.\n" +
        "Use when the user asks: draft an SOW for <project>; produce a risk summary; " +
        "create a cost-estimate scaffold; write a funding-positioning memo.\n" +
        "Do NOT use for: free-form text generation (just answer directly — the agent " +
        "LLM is the right tool); detailed cost models (this is a scaffold only); " +
        "documents outside the 4 supported types.\n" +
        "document_type semantics:\n" +
        "  'scope_of_work' → SOW with sections: scope, deliverables, schedule, " +
        "exclusions, assumptions\n" +
        "  'risk_summary' → Top-5 risks ranked by likelihood × impact with mitigation " +
        "language\n" +
        "  'cost_estimate_scaffold' → line-item table with placeholder costs the " +
        "analyst fills in\n" +
        "  'funding_positioning_memo' → grant / funding pursuit memo with eligibility, " +
        "match requirements, key differentiators")]
    public async Task<string> DraftDocumentAsync(
        [Description("REQUIRED. 'scope_of_work' | 'risk_summary' | 'cost_estimate_scaffold' | 'funding_positioning_memo'.")] string document_type,
        [Description("Context object from prior tool calls — e.g. {bridges:[...], water_systems:[...], contract_awards:[...], best_practices:[...]}. Template fields reference keys in this object.")] JsonElement context,
        [Description("Project name — e.g. 'IH-35 Bridge Rehabilitation Phase II'. Renders into document header.")] string? project_name = null,
        [Description("Client name — e.g. 'TxDOT Austin District'. Renders into document header.")] string? client_name = null,
        [Description("Analyst notes — free text appended to relevant sections. Use for caveats, assumptions, or client-specific context.")] string? notes = null,
        CancellationToken cancellationToken = default)
    {
        if (!TemplateMap.TryGetValue(document_type, out var resourceName))
            return JsonSerializer.Serialize(new { error = $"Unknown document_type: '{document_type}'", source = "draft_document", retriable = false });

        try
        {
            var templateText = LoadTemplate(resourceName);
            var template = Template.Parse(templateText);

            // Build Scriban script object from context
            var scriptObject = new ScriptObject();
            scriptObject["project_name"] = project_name ?? "";
            scriptObject["client_name"] = client_name ?? "";
            scriptObject["notes"] = notes ?? "";
            scriptObject["generated_date"] = DateTime.UtcNow.ToString("yyyy-MM-dd");

            // Convert context JsonElement to a Scriban-accessible object
            var contextObj = JsonElementToScriptObject(context);
            scriptObject["context"] = contextObj;

            var templateContext = new TemplateContext();
            templateContext.PushGlobal(scriptObject);

            var rendered = await template.RenderAsync(templateContext);
            return rendered;
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "draft_document failed for type {DocumentType}", document_type);
            return JsonSerializer.Serialize(new { error = ex.Message, source = "draft_document", retriable = false });
        }
    }

    private static string LoadTemplate(string resourceName)
    {
        var assembly = Assembly.GetExecutingAssembly();
        using var stream = assembly.GetManifestResourceStream(resourceName)
            ?? throw new InvalidOperationException($"Embedded resource '{resourceName}' not found.");
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    private static object? JsonElementToScriptObject(JsonElement element)
    {
        return element.ValueKind switch
        {
            JsonValueKind.Object => ConvertObject(element),
            JsonValueKind.Array => ConvertArray(element),
            JsonValueKind.String => element.GetString(),
            JsonValueKind.Number => element.TryGetInt64(out var i) ? (object)i : element.GetDouble(),
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.Null or JsonValueKind.Undefined => null,
            _ => null,
        };
    }

    private static ScriptObject ConvertObject(JsonElement element)
    {
        var obj = new ScriptObject();
        foreach (var prop in element.EnumerateObject())
        {
            // Scriban accesses properties directly; use the property name as-is
            obj[prop.Name] = JsonElementToScriptObject(prop.Value);
        }
        return obj;
    }

    private static ScriptArray ConvertArray(JsonElement element)
    {
        var arr = new ScriptArray();
        foreach (var item in element.EnumerateArray())
        {
            arr.Add(JsonElementToScriptObject(item));
        }
        return arr;
    }
}
