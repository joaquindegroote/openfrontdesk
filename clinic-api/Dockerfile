# Build context: repository root (the clinic API is a member of the uv workspace).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY engine/pyproject.toml engine/pyproject.toml
COPY clinic-api/pyproject.toml clinic-api/pyproject.toml
COPY evals/pyproject.toml evals/pyproject.toml
RUN uv sync --frozen --no-dev --package clinic-api --no-install-workspace

COPY clinic-api clinic-api
RUN uv sync --frozen --no-dev --package clinic-api

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8001
CMD ["uvicorn", "clinic_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
