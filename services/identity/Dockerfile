FROM python:3.12-slim

WORKDIR /repo/services/identity

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/identity/pyproject.toml services/identity/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/identity/config.yaml .
COPY services/identity/src/ src/

EXPOSE 8001

CMD ["uv", "run", "uvicorn", "identity_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8001"]
