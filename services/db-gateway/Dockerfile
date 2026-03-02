FROM python:3.12-slim AS base

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy dependency files
COPY pyproject.toml ./
COPY ../../libs/service-commons /libs/service-commons

# Install dependencies
RUN uv sync --no-dev --frozen

# Copy application code
COPY src/ ./src/
COPY config.yaml ./

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "db_gateway_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
