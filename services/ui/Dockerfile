FROM python:3.12-slim

WORKDIR /repo/services/ui

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/ui/pyproject.toml services/ui/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/ui/config.yaml .
COPY services/ui/src/ src/
COPY services/ui/data/web data/web

EXPOSE 8008

CMD ["uv", "run", "uvicorn", "ui_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8008"]
