FROM ghcr.io/astral-sh/uv:0.8.22-python3.11-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml .python-version ./
COPY src ./src
COPY proto ./proto

RUN uv sync --no-dev

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/proto /app/proto

ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONPATH="/app/src"

CMD ["uvicorn", "aetus_ingest.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
