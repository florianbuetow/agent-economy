FROM python:3.12-slim

WORKDIR /repo/services/observatory

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/observatory/pyproject.toml services/observatory/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/observatory/config.yaml .
COPY services/observatory/src/ src/

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "observatory_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
