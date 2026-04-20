FROM python:3.12-slim

# Install uv
RUN pip install --no-cache-dir uv==0.4.18

WORKDIR /app

COPY pyproject.toml .
RUN uv pip install --system --no-cache -e .

COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# Git metadata for Datadog source code integration
ARG DD_GIT_REPOSITORY_URL
ARG DD_GIT_COMMIT_SHA
ENV DD_GIT_REPOSITORY_URL=${DD_GIT_REPOSITORY_URL}
ENV DD_GIT_COMMIT_SHA=${DD_GIT_COMMIT_SHA}

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
