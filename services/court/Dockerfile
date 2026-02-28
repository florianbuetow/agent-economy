FROM python:3.12-slim

WORKDIR /repo/services/court

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/court/pyproject.toml services/court/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/court/config.yaml .
COPY services/court/src/ src/

EXPOSE 8005

CMD ["uv", "run", "uvicorn", "court_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8005"]
