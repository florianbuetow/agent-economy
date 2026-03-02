FROM python:3.12-slim

WORKDIR /repo/services/central-bank

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/central-bank/pyproject.toml services/central-bank/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/central-bank/config.yaml .
COPY services/central-bank/src/ src/

EXPOSE 8002

CMD ["uv", "run", "uvicorn", "central_bank_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8002"]
