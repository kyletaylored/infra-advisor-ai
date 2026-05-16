namespace InfraAdvisor.AgentApi.Models;

// Stream events emitted on POST /query/stream as Server-Sent Events. Each
// event is serialized to its own SSE block:
//   event: <EventName>
//   data: <JSON of the record fields, omitting EventName>
//
// EventName matches the lowercase record discriminator the UI listens for
// (step / tool_call_start / tool_call_end / text_chunk / done / error).
public abstract record StreamEvent(string EventName);

// Internal pipeline steps that aren't LLM tool calls — classify_domain,
// retrieve_best_practices, etc. The UI renders these as the same chip
// style for visual consistency.
public sealed record StepEvent(
    string Step,
    string Status,                // "running" | "done" | "skipped"
    string? Detail = null         // e.g. "engineering" for classify, "3 docs" for retrieval
) : StreamEvent("step");

// Model decided to call a tool. Args are the JSON the model sent — the UI
// renders them in the expandable detail row.
public sealed record ToolCallStartEvent(
    string Id,
    string Name,
    string? ArgsJson
) : StreamEvent("tool_call_start");

// Tool returned. Result_summary is intentionally short (max ~80 chars)
// for the chip; the full result is whatever the model already has. The
// Sources list is _source fields the tool surfaced, attributed to this
// specific tool so the UI can render origin attribution per chip.
public sealed record ToolCallEndEvent(
    string Id,
    string Name,
    string Status,                // "ok" | "error"
    string? ResultSummary,
    List<string> Sources,
    double DurationMs
) : StreamEvent("tool_call_end");

// Assistant message token chunk — append to the current message.
public sealed record TextChunkEvent(
    string Chunk
) : StreamEvent("text_chunk");

// Terminal event with trace + metadata the UI uses for the message
// footer (feedback button, trace link, sources reconciliation).
public sealed record DoneEvent(
    string? TraceId,
    string? SpanId,
    string SessionId,
    string Model,
    List<string> Sources,
    List<string> ToolsCalled,
    string QueryDomain
) : StreamEvent("done");

// Surfaced when something fatal happens mid-stream. The UI shows the
// error and stops the stream — same UX as the existing /query 5xx path.
public sealed record ErrorEvent(
    string Message,
    string? TraceId
) : StreamEvent("error");
