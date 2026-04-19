FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.4.18

WORKDIR /app

COPY pyproject.toml .
RUN uv pip install --system --no-cache -e .

COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

CMD ["python", "src/main.py"]
