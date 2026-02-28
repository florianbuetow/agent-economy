FROM python:3.12-slim

WORKDIR /repo/services/reputation

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/reputation/pyproject.toml services/reputation/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/reputation/config.yaml services/reputation/docker-config.yaml ./
COPY services/reputation/src/ src/

EXPOSE 8004

CMD ["uv", "run", "uvicorn", "reputation_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8004"]
