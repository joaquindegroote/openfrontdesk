# Build context: repository root (the engine is a member of the uv workspace).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock .python-version ./
COPY engine/pyproject.toml engine/pyproject.toml
COPY clinic-api/pyproject.toml clinic-api/pyproject.toml
RUN uv sync --frozen --no-dev --package openfrontdesk --no-install-workspace

COPY engine engine
COPY businesses businesses
RUN uv sync --frozen --no-dev --package openfrontdesk

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "openfrontdesk.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
