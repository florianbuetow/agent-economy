FROM node:22-slim AS frontend-build

WORKDIR /frontend
COPY services/observatory/frontend/package.json services/observatory/frontend/package-lock.json ./
RUN npm ci
COPY services/observatory/frontend/ .
RUN npm run build

FROM python:3.12-slim

WORKDIR /repo/services/observatory

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/observatory/pyproject.toml services/observatory/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/observatory/config.yaml .
COPY services/observatory/src/ src/
COPY --from=frontend-build /frontend/dist frontend/dist/

EXPOSE 8006

CMD ["uv", "run", "uvicorn", "observatory_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8006"]
