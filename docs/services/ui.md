---
title: UI
parent: Services
nav_order: 5
---

# React UI

**Framework:** React 18 + TypeScript 5.6 + Chakra UI v3 | **Build:** Vite | **Served by:** nginx

The UI is a single-page application (SPA) that provides the consultant-facing chat interface. It handles authentication, query submission, response rendering, citation browsing, feedback collection, admin user management, and session management.

## Features

### Chat interface

The core workflow:
1. User submits a natural-language query
2. `POST /api/query` with `Authorization` and `X-Session-ID` headers
3. Response streams back with answer, citations, trace ID, and model used
4. Follow-up suggestions appear below the answer
5. Citation panel expands on the right with tool sources and external links

### Session persistence

Session IDs are stored in `localStorage` under the key `infra_advisor_session_id`. Page reloads resume the same conversation — same Redis memory key, same LLM Obs session grouping.

The **New Conversation** button (pencil icon in header) generates a fresh UUID and stores it, clearing all in-progress conversation state.

### Model picker

The query input bar shows pill buttons for each available model:

```
[ gpt-4.1-mini ]  [ gpt-4.1 ]
```

The selected model is sent with each query. The Agent API persists it to Redis, so the same model is used for follow-up turns even without the user re-selecting.

The response includes the model used, so the UI syncs on page reload (session recovery from Redis).

### Citation panel

Each AI response includes a `sources` list. The sidebar shows source cards with:
- Tool name and icon (color-coded by domain)
- Expandable detail panel with data notes
- "View data source →" link to the external dataset (FHWA, OpenFEMA, EIA, etc.)

### Feedback

Every AI message shows three action icons:
- **👍 Thumbs up** → `POST /api/feedback {rating: "positive"}`
- **👎 Thumbs down** → `POST /api/feedback {rating: "negative"}`
- **🚩 Flag** → `POST /api/feedback {rating: "reported"}`
- **↗ Trace link** → opens the Datadog APM trace for this specific response in a new tab

Feedback is submitted as a Datadog LLM Observability evaluation and appears under the **Evaluations** tab on each trace.

### Domain tiles

The empty state shows four clickable domain tiles:

| Icon | Domain | Starter query |
|------|--------|--------------|
| Gauge | Engineering | Bridge condition + ADT query |
| HardHat | Construction | Infrastructure procurement query |
| ShieldCheck | Resilience | Disaster history + repeat county query |
| Briefcase | Advisory | Contract awards query |

Clicking a tile auto-submits its starter query without requiring the user to type.

### Suggestion pool

On page load, the UI calls `GET /api/suggestions/initial` to populate 4 opening suggestion cards. These are drawn from a Redis pool of up to 80 AECOM-focused suggestions. After each response, `POST /api/suggestions` generates 4 follow-up suggestions based on the conversation context.

### Sandbox playground

The **Sandbox** tab lets users invoke MCP tools directly without going through the agent. It includes a JSON parameter editor and response viewer. For `POST /query` and `search_project_knowledge`, a **Suggest** button calls `/api/suggestions` to auto-populate a sample query.

### Admin panel

Visible only to admin users. Supports:
- View all registered users
- Create user accounts (any email domain)
- Delete users (cannot delete own account)
- Toggle admin / service account flags

### Guided tour

On first login, a 7-step Driver.js tour walks through:
1. Welcome overlay
2. Domain tiles
3. Chat input
4. Suggestion pills
5. Citation sidebar
6. Sandbox tab
7. Tour restart button

The tour can be re-triggered any time via the Compass icon in the header. Completion state is stored in `localStorage`.

## Component structure

```
src/
  App.tsx                   Root component, auth state, routing
  components/
    LoginPage.tsx           Login, register, forgot/reset password flows
    Chat.tsx                Main conversation UI, query submission, suggestions
    CitationPanel.tsx       Sidebar showing tool sources with expand/link
    AdminPanel.tsx          User management table (admin only)
    Sandbox.tsx             Direct MCP tool invocation playground
  lib/
    api.ts                  HTTP client (sendQuery, fetchModels, submitFeedback, etc.)
    auth.ts                 Auth API wrappers (login, register, forgotPassword, etc.)
    datadog-rum.ts          RUM initialization, getRumSessionId()
    tour.ts                 Driver.js tour definition, localStorage helpers
```

## Datadog RUM

The browser SDK (`@datadog/browser-rum`) is initialized in `datadog-rum.ts` with session replay enabled.

**Custom events tracked:**

| Event | When | Attributes |
|-------|------|-----------|
| `query_submitted` | User submits a query | `query` text, `domain` |
| `citation_expanded` | User expands a source card | `tool_name` |
| `suggestion_clicked` | User clicks a suggestion | `label` text |

**Session→trace linking:**

`getRumSessionId()` reads `datadogRum.getInternalContext()?.session_id`. This ID is sent as `X-DD-RUM-Session-ID` on every query request. The Agent API sets it as `session.id` on all LLM Obs spans, enabling:
- Jump from LLM Obs trace → RUM session replay
- Filter LLM Obs by RUM session ID
- See which queries were triggered during a specific browser session

**Sourcemaps:** After each build, sourcemaps are uploaded to Datadog via `@datadog/datadog-ci` so error stack traces in RUM show original TypeScript source lines.

## Build

```bash
cd services/ui
npm install
npm run build      # outputs to dist/
npm run dev        # local dev server (Vite, hot reload)
```

**Environment variables** (set at build time via Vite):

| Variable | Description |
|----------|-------------|
| `VITE_DD_RUM_APP_ID` | Datadog RUM Application ID |
| `VITE_DD_RUM_CLIENT_TOKEN` | Datadog RUM client token |
| `VITE_DD_RUM_SITE` | Datadog site (us3.datadoghq.com) |

These are injected by GitHub Actions from repository secrets during the Docker image build.

## nginx reverse proxy

The production Docker image uses nginx to:
1. Serve the built React SPA (`dist/`) as static files
2. Proxy `/api/*` to `agent-api.infra-advisor.svc.cluster.local:8001`
3. Proxy `/auth/*` to `auth-api.infra-advisor.svc.cluster.local:8002`
4. Proxy `/airflow/*` to `airflow-api-server.airflow.svc.cluster.local:8080`
5. Proxy `/mailhog/*` to `mailhog.infra-advisor.svc.cluster.local:8025`
6. Serve the SPA for all unknown paths (`try_files $uri $uri/ /index.html`)

Static assets (`/assets/`) are cached with `Cache-Control: public, immutable` and a 1-year `Expires` header.
