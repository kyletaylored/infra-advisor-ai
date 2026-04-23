---
title: RUM & Session Replay
parent: Observability
nav_order: 3
---

# RUM & Session Replay

Datadog Real User Monitoring captures every browser session with full session replay, performance metrics, and custom event tracking. It links directly to backend LLM Observability traces so you can jump from a user's browser session to the exact agent reasoning trace that produced a response.

Navigate to **Datadog → RUM → Sessions** to explore.

## Initialization

The RUM SDK is initialized in `services/ui/src/lib/datadog-rum.ts`:

```typescript
datadogRum.init({
  applicationId: import.meta.env.VITE_DD_RUM_APP_ID,
  clientToken: import.meta.env.VITE_DD_RUM_CLIENT_TOKEN,
  site: import.meta.env.VITE_DD_RUM_SITE,   // us3.datadoghq.com
  service: 'infra-advisor-ui',
  env: 'dev',
  version: '<build-sha>',
  sessionSampleRate: 100,
  sessionReplaySampleRate: 100,
  trackUserInteractions: true,
  trackResources: true,
  trackLongTasks: true,
  defaultPrivacyLevel: 'mask-user-input',  // hides typed text in replay
})
datadogRum.startSessionReplayRecording()
```

## Custom events

Three custom events are tracked explicitly:

| Event name | Triggered when | Attributes |
|------------|---------------|-----------|
| `query_submitted` | User submits a query (send button or Enter) | `query` (text), `domain` (routed domain) |
| `citation_expanded` | User clicks a citation card to expand it | `tool_name` (e.g., `get_bridge_condition`) |
| `suggestion_clicked` | User clicks a suggestion pill or domain tile | `label` (suggestion text) |

These appear in RUM session timelines and are queryable in RUM Analytics.

## RUM → LLM Obs session linking

Every query request includes the RUM session ID as a custom header:

```typescript
// lib/datadog-rum.ts
export function getRumSessionId(): string | undefined {
  return datadogRum.getInternalContext()?.session_id
}

// lib/api.ts
headers: {
  'X-DD-RUM-Session-ID': getRumSessionId() ?? ''
}
```

The Agent API sets this as `session.id` on all LLM Obs spans:

```python
obs_session_id = rum_session_id or session_id
LLMObs.annotate(span, tags={"session.id": obs_session_id})
```

**Result:** In LLM Obs, every trace shows the RUM session ID as `session.id`. Clicking **"View Session Replay"** on a trace opens the corresponding browser recording. In RUM sessions, clicking an XHR event on the `/api/query` call opens the backend trace.

## Distributed tracing (RUM → APM)

The Datadog RUM SDK auto-injects distributed trace headers on outgoing XHR/fetch requests matching `allowedTracingUrls`. For requests to `/api/*`, the SDK injects:

```
X-Datadog-Trace-Id: 3421959702764693
X-Datadog-Parent-Id: 8721043291846321
X-Datadog-Origin: rum
X-Datadog-Sampling-Priority: 1
```

The Agent API's ddtrace picks up these headers, continuing the same trace. The `trace_id` in the query response is the same trace ID injected by RUM.

This creates a single trace that spans from the browser click through the nginx proxy, FastAPI, Redis, LangChain agent, MCP tool calls, and back.

## Sourcemaps

After each production build, sourcemaps are uploaded to Datadog so that JavaScript errors in RUM show the original TypeScript source file and line number (not the minified bundle):

```bash
npx @datadog/datadog-ci sourcemaps upload dist/ \
  --service=infra-advisor-ui \
  --release-version=<sha> \
  --minified-path-prefix=/assets/
```

This is run automatically by the GitHub Actions build pipeline.

## Performance monitoring

RUM automatically captures:
- **Core Web Vitals:** LCP, INP, CLS on every page load
- **Resource timing:** Load times for JS/CSS bundles
- **Long tasks:** Any main thread work > 50ms
- **User interactions:** Click/scroll events with timing

The primary user interaction to monitor is the **Time To First Token** — the latency between query submission and the first character of the answer appearing. This corresponds to the `POST /api/query` XHR duration in RUM resource timing.
