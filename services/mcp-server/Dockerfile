FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies (no dev extras)
RUN uv sync --no-dev --frozen

# Copy application source
COPY src/ ./src/

# DD_VERSION injected at build time by CI (defaults to "local")
ARG DD_VERSION=local
ENV DD_VERSION=${DD_VERSION}

# Service identification (overridden per-pod in K8s via env)
ENV DD_SERVICE=infratools-mcp
ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
