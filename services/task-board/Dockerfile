FROM python:3.12-slim

WORKDIR /repo/services/task-board

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install uv

COPY libs/service-commons/ /repo/libs/service-commons/
COPY services/task-board/pyproject.toml services/task-board/uv.lock ./
RUN uv sync --frozen --no-dev

COPY services/task-board/config.yaml .
COPY services/task-board/src/ src/

EXPOSE 8003

CMD ["uv", "run", "uvicorn", "task_board_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8003"]
