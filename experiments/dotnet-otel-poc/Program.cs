// Minimal .NET OTel GenAI POC, modeled on the official dotnet/ai-samples
// Azure OpenAI example. Microsoft.Extensions.AI's UseOpenTelemetry() emits
// the gen_ai.* chat span; UseFunctionInvocation() emits execute_tool spans.
// We just need an OTel TracerProvider configured to listen on the same
// ActivitySource M.E.AI uses ("Experimental.Microsoft.Extensions.AI") and
// export OTLP to the collector.

using System.ComponentModel;
using Azure;
using Azure.AI.OpenAI;
using Microsoft.AspNetCore.Mvc;
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
var serviceName  = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")        ?? "otel-genai-poc";
var otlpEndpoint = Environment.GetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT") ?? "http://localhost:4318";

// ── Chat client pipeline (M.E.AI builder pattern) ─────────────────────────────
builder.Services.AddSingleton(new AzureOpenAIClient(
    new Uri(endpoint), new AzureKeyCredential(apiKey)));

builder.Services
    .AddChatClient(services => services
        .GetRequiredService<AzureOpenAIClient>()
        .GetChatClient(deployment)
        .AsIChatClient())
    .UseFunctionInvocation()
    .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true);

// ── OpenTelemetry ─────────────────────────────────────────────────────────────
// "Experimental.Microsoft.Extensions.AI" is the ActivitySource name M.E.AI's
// OpenTelemetryChatClient uses internally. Without it in AddSource, the chat
// + execute_tool spans never reach the exporter.
builder.Services.AddOpenTelemetry()
    .ConfigureResource(r => r.AddService(serviceName))
    .WithTracing(t => t
        .AddAspNetCoreInstrumentation()
        .AddSource("Experimental.Microsoft.Extensions.AI")
        .AddOtlpExporter(o =>
        {
            o.Endpoint = new Uri($"{otlpEndpoint.TrimEnd('/')}/v1/traces");
            o.Protocol = OtlpExportProtocol.HttpProtobuf;
        }));

// ── Tools ─────────────────────────────────────────────────────────────────────
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

// ── App ──────────────────────────────────────────────────────────────────────
var app = builder.Build();
app.UseDefaultFiles();
app.UseStaticFiles();

// Browser RUM config — values come from server env so no secrets in HTML.
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

app.MapPost("/chat", async (IChatClient client, [FromBody] ChatRequest body) =>
{
    var messages = new List<ChatMessage>
    {
        new(ChatRole.System, "You are a friendly assistant. Use the available tools when relevant. Be concise."),
        new(ChatRole.User, body.Query),
    };
    var response = await client.GetResponseAsync(messages, new ChatOptions { Tools = tools });
    return Results.Ok(new { answer = response.Text ?? "" });
});

app.Run();

record ChatRequest(string Query, string? SessionId);
