FROM ghcr.io/astral-sh/uv:0.8.22-python3.11-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN uv sync --no-dev

FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONPATH="/app/src"

CMD ["uvicorn", "aetus_query.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
