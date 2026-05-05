// Intentionally empty — LlmTelemetry is now sourced directly from OpenInference.NET.Core.
// AgentService.cs imports OpenInference.NET.Core and calls LlmTelemetry.StartLlmActivity /
// EndLlmActivity on the library class, which emits spans under ActivitySource "OpenInference.NET".
// The DD SDK bridge (DD_TRACE_OTEL_ENABLED=true) captures that source automatically.
