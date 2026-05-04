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
        "Generate a structured document scaffold (SOW, risk summary, cost estimate, or funding memo). " +
        "document_type must be one of: 'scope_of_work', 'risk_summary', 'cost_estimate_scaffold', 'funding_positioning_memo'.")]
    public async Task<string> DraftDocumentAsync(
        [Description("Document type: 'scope_of_work', 'risk_summary', 'cost_estimate_scaffold', or 'funding_positioning_memo'")] string document_type,
        [Description("Context dictionary from previous tool calls (bridges, water_systems, etc.)")] JsonElement context,
        [Description("Project name")] string? project_name = null,
        [Description("Client name")] string? client_name = null,
        [Description("Additional analyst notes")] string? notes = null,
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
