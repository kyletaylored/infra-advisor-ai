---
title: Testing
parent: Development
nav_order: 2
---

# Testing

## Test coverage

| Service | Framework | Tests | Command |
|---------|-----------|-------|---------|
| MCP Server | pytest + respx | 79 tests | `make test-mcp` |
| Agent API | pytest + respx + pytest-asyncio | 23 tests | `make test-agent` |
| Auth API | pytest + httpx | 11 tests | `uv run pytest services/auth-api/tests/` |
| Load Generator | pytest | — | `make test-load-gen` |
| UI | TypeScript compiler | 0 type errors | `npx tsc --noEmit` |

Run all at once:
```bash
make test-all
```

## MCP Server tests (`services/mcp-server/tests/`)

Each tool has a dedicated test file. All external HTTP calls are mocked with `respx`.

```bash
cd services/mcp-server
uv run pytest tests/ -v
```

**Test structure for each tool:**
- `test_successful_results` — happy path with realistic API mock response
- `test_empty_results` — API returns no records (graceful empty list)
- `test_api_error` — upstream returns 4xx/5xx (structured error dict returned)
- `test_parameter_filtering` — verify query params passed correctly
- Tool-specific edge cases (e.g., `test_date_range_clamped` for SAM.gov, `test_content_passed_directly_to_extraction` for Tavily)

**Mocking pattern:**
```python
import respx
import httpx
import pytest

@pytest.mark.asyncio
async def test_get_bridge_condition_success():
    mock_response = {"features": [{"attributes": {...}}]}
    with respx.mock:
        respx.get("https://services.arcgis.com/...").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        result = await get_bridge_condition(state="TX", county="Harris")
    assert len(result) > 0
    assert result[0]["state"] == "TX"
```

## Agent API tests (`services/agent-api/tests/`)

Agent tests mock the MCP Server HTTP calls and Azure OpenAI responses. The full LangChain ReAct loop is tested end-to-end with mocked LLM responses.

```bash
cd services/agent-api
uv run pytest tests/ -v --timeout=120
```

**Test runtime:** ~70–80 seconds (LangChain agent setup has overhead even with mocks).

**Key test cases:**
- Router domain classification (5 domains)
- Specialist tool subset selection
- Session memory persistence
- Model selection (request → session → default precedence)
- Feedback endpoint (valid/invalid ratings, 204/422 responses)
- `/suggestions/initial` with and without pool
- Error handling (MCP unavailable, Redis down)

## Auth API tests (`services/auth-api/tests/`)

```bash
cd services/auth-api
uv run pytest tests/ -v
```

**Password reset tests (`test_password_reset.py`):** 11 tests covering:
- `forgot-password` with existing user, unknown email, email normalization
- `reset-password` with valid token, invalid token, expired token, short password, hash verification
- Auth helpers: token uniqueness, hash determinism, hash ≠ plaintext

## TypeScript build

The UI has no runtime tests — coverage comes from TypeScript strict type checking:

```bash
cd services/ui
npm install
npx tsc --noEmit     # type check
npm run build        # Vite production build (catches import errors)
```

## CI pipeline

GitHub Actions runs on every PR and push to `main`:

```yaml
# .github/workflows/ci.yml
matrix:
  service: [mcp-server, agent-api]

steps:
  - uv sync
  - uv run pytest tests/ --timeout=120
```

The CI job injects mock environment variables for Azure credentials so tests run without real API access.

## Running a specific test

```bash
# Run a single test file
uv run pytest tests/test_bridge_condition.py -v

# Run a single test
uv run pytest tests/test_bridge_condition.py::test_get_bridge_condition_success -v

# Run with stdout (see print statements)
uv run pytest tests/ -s -v

# Stop on first failure
uv run pytest tests/ -x
```

## Test environment variables

Tests use `respx` to mock HTTP calls, so Azure API keys are not needed for unit tests. However, some env vars must be set (any non-empty value works):

```bash
AZURE_OPENAI_ENDPOINT=https://test.openai.azure.com/
AZURE_OPENAI_API_KEY=test-key
AZURE_SEARCH_ENDPOINT=https://test.search.windows.net/
AZURE_SEARCH_API_KEY=test-key
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=test;AccountKey=dGVzdA==;EndpointSuffix=core.windows.net
EIA_API_KEY=test-key
```
