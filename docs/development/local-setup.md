---
title: Local Setup
parent: Development
nav_order: 1
---

# Local Setup

Run InfraAdvisor AI locally without an AKS cluster. You'll need Redis and Kafka running locally (via Docker), Azure credentials for OpenAI/Search/Storage, and each service started separately.

## 1. Clone and configure

```bash
git clone https://github.com/kyletaylored/infra-advisor-ai.git
cd infra-advisor-ai
cp .env.example .env
# Fill in Azure OpenAI, Search, Storage keys at minimum
set -a && source .env && set +a
```

## 2. Start Redis and Kafka

```bash
docker compose up -d
```

The `docker-compose.yml` starts:
- Redis on `localhost:6379`
- Kafka on `localhost:9092`
- Zookeeper (Kafka dependency)

## 3. Start MCP Server

```bash
cd services/mcp-server
uv sync
uv run uvicorn src.main:app --reload --port 8000
```

Verify:
```bash
curl http://localhost:8000/health | python3 -m json.tool
```

## 4. Start Agent API (Python)

```bash
cd services/agent-api
uv sync
MCP_SERVER_URL=http://localhost:8000/mcp \
REDIS_HOST=localhost \
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
uv run uvicorn src.main:app --reload --port 8001
```

Verify:
```bash
curl http://localhost:8001/health | python3 -m json.tool
```

## 4b. Start Agent API (.NET) — optional

Requires the [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0).

```bash
cd services/agent-api-dotnet
dotnet restore

AZURE_OPENAI_ENDPOINT=<your-endpoint> \
AZURE_OPENAI_API_KEY=<your-key> \
MCP_SERVER_URL=http://localhost:8000/mcp \
REDIS_HOST=localhost \
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
dotnet run --urls http://localhost:8003
```

The .NET backend listens on port 8003 locally (the Python backend owns 8001). In the cluster both listen on 8001 in separate pods.

Verify:
```bash
curl http://localhost:8003/health | python3 -m json.tool
```

> **Conversation persistence (.NET):** Add `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/infraadvisor` to the environment above. Tables are created automatically on startup.

## 4c. Start MCP Server (.NET) — optional

```bash
cd services/mcp-server-dotnet
dotnet restore

AZURE_OPENAI_ENDPOINT=<your-endpoint> \
AZURE_OPENAI_API_KEY=<your-key> \
AZURE_SEARCH_ENDPOINT=<your-endpoint> \
AZURE_SEARCH_API_KEY=<your-key> \
dotnet run --urls http://localhost:8004
```

Then point the .NET Agent API at it: `MCP_SERVER_URL=http://localhost:8004/mcp`.

## 5. Start Auth API

```bash
cd services/auth-api
uv sync
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/infraadvisor \
uv run uvicorn src.main:app --reload --port 8002
```

Or use SQLite for local dev (modify `database.py` connection string):
```python
engine = create_engine("sqlite:///./auth.db")
```

## 6. Start the UI

```bash
cd services/ui
npm install
npm run dev
```

Opens at `http://localhost:5173`.

The Vite dev server proxies `/api/*` → `localhost:8001` and `/auth/*` → `localhost:8002` (configured in `vite.config.ts`).

## 7. Test a query

1. Register at `http://localhost:5173`
2. Ask: `"Show bridges in Harris County TX with sufficiency below 50"`

## Running without all dependencies

You can run the MCP Server standalone to test individual tools:

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_bridge_condition","arguments":{"state":"TX","county":"Harris","max_sufficiency":50,"limit":5}}}'
```

## Environment variable tips

For local dev, keep a `.env.local` for machine-specific overrides:

```bash
# .env.local (not committed)
MCP_SERVER_URL=http://localhost:8000/mcp
REDIS_HOST=localhost
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/infraadvisor
```

Source both files:
```bash
set -a && source .env && source .env.local && set +a
```

## Vite dev server proxy

`vite.config.ts` proxies both backends:

| Browser path | Proxied to |
|-------------|-----------|
| `/api/*` | `http://localhost:8001` (Python Agent API) |
| `/api-dotnet/*` | `http://localhost:8003` (.NET Agent API) |
| `/auth/*` | `http://localhost:8002` (Auth API) |

Switch backends in the UI using the **Python / .NET** toggle in the chat toolbar.

## Disabling Datadog in local dev

`ddtrace.auto` is a no-op when no Datadog Agent is reachable — it just produces warnings. To suppress:

```bash
DD_TRACE_ENABLED=false uv run uvicorn src.main:app --reload --port 8001
```
