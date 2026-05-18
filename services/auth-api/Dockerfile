FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
RUN uv pip install --system --no-cache .

COPY src/ ./src/

ENV PYTHONPATH=/app/src

RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/false appuser
USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
