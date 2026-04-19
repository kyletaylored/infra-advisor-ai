FROM python:3.12-slim

# Install uv
RUN pip install --no-cache-dir uv==0.4.18

WORKDIR /app

COPY pyproject.toml .
RUN uv pip install --system --no-cache -e .

COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
