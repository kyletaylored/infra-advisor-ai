// Auto-instrumented variant of the .NET OTel GenAI POC.
//
// Zero OpenTelemetry SDK code in this file. The OTel .NET
// auto-instrumentation profiler (installed under ~/.otel-dotnet-auto/)
// attaches at process startup via CORECLR_* env vars, registers a
// TracerProvider that listens to every ActivitySource, and configures
// the OTLP exporter from standard OTEL_* env vars.
//
// The only telemetry-aware line in this Program.cs is .UseOpenTelemetry()
// on the IChatClient builder — that's M.E.AI emitting `chat` and
// `execute_tool` spans with OTel GenAI semantic conventions. The
// profiler picks them up automatically.

using System.ComponentModel;
using Azure;
using Azure.AI.OpenAI;
using Microsoft.Extensions.AI;

var builder = WebApplication.CreateBuilder(args);

// ── Config ────────────────────────────────────────────────────────────────────
var endpoint   = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT")   ?? throw new("AZURE_OPENAI_ENDPOINT not set");
var apiKey     = Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY")    ?? throw new("AZURE_OPENAI_API_KEY not set");
var deployment = Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT") ?? "gpt-4.1-mini";

// ── Chat client pipeline (M.E.AI builder pattern) ─────────────────────────────
builder.Services.AddSingleton(new AzureOpenAIClient(
    new Uri(endpoint), new AzureKeyCredential(apiKey)));

builder.Services
    .AddChatClient(s => s.GetRequiredService<AzureOpenAIClient>()
        .GetChatClient(deployment)
        .AsIChatClient())
    .UseFunctionInvocation()
    .UseOpenTelemetry(configure: cfg => cfg.EnableSensitiveData = true);

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
    var svc      = Environment.GetEnvironmentVariable("OTEL_SERVICE_NAME")     ?? "otel-genai-poc-autoinstr";
    var js = $$"""
        window.RUM_CONFIG = {
          applicationId: "{{rumAppId}}",
          clientToken: "{{rumToken}}",
          site: "{{ddSite}}",
          service: "{{svc}}",
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

app.MapPost("/chat", async (IChatClient client, ChatRequest body) =>
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
