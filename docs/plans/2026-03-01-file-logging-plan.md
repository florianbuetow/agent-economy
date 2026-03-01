# Rotating File Logging — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add daily rotating file logging (YYYY-MM-DD.log) to all services via the shared service-commons library.

**Architecture:** Extend `setup_logging()` in `libs/service-commons` to accept a `log_directory` parameter. When provided, add a `TimedRotatingFileHandler` alongside the existing stdout handler. Each service passes `settings.logging.directory` from its `config.yaml`. Remove the unused `format` field from all configs.

**Tech Stack:** Python stdlib `logging.handlers.TimedRotatingFileHandler`, Pydantic config models, existing `service_commons.logging` module.

**Design doc:** `docs/plans/2026-03-01-file-logging-design.md`

**Key constraint:** No default values, no fallback values anywhere. All config is explicit.

**Tests are acceptance tests — do NOT modify existing test files.**

---

## Reference Files

- `libs/service-commons/src/service_commons/logging.py` — shared logging module (the core change)
- `services/*/src/*/config.py` — each has `LoggingConfig` with `level: str` and `format: str` (lines 43-48)
- `services/*/src/*/core/lifespan.py` — each calls `setup_logging(settings.logging.level, settings.service.name)`
- `services/*/config.yaml` — each has `logging.level` and `logging.format`

---

### Task 1: Update service-commons setup_logging()

**Files:**
- Modify: `libs/service-commons/src/service_commons/logging.py`

**Step 1: Read the current file**

Read `libs/service-commons/src/service_commons/logging.py` in full.

**Step 2: Add the `log_directory` parameter and file handler**

Replace the `setup_logging` function with:

```python
def setup_logging(level: str, service_name: str, log_directory: str) -> logging.Logger:
    """
    Configure structured JSON logging for a service.

    Logs to both stdout and a daily rotating file in log_directory.
    File naming: YYYY-MM-DD.log.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        service_name: Name of the service for logger identification
        log_directory: Directory for rotating log files

    Returns:
        Configured logger instance

    Raises:
        ValueError: If level is not a valid log level
    """
    level_upper = level.upper()
    if level_upper not in VALID_LOG_LEVELS:
        raise ValueError(f"Invalid log level: {level}. Must be one of {sorted(VALID_LOG_LEVELS)}")

    numeric_level = getattr(logging, level_upper)

    logger = logging.getLogger(service_name)
    logger.setLevel(numeric_level)
    logger.handlers.clear()

    formatter = JSONFormatter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(numeric_level)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)

    os.makedirs(log_directory, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_directory, "current.log"),
        when="midnight",
        utc=True,
    )
    file_handler.namer = _log_namer
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger
```

**Step 3: Add the namer function and imports**

Add at the top of the file:

```python
import os
from logging.handlers import TimedRotatingFileHandler
```

Add the namer function before `setup_logging`:

```python
def _log_namer(default_name: str) -> str:
    """
    Rename rotated log files to YYYY-MM-DD.log format.

    TimedRotatingFileHandler appends a date suffix to the base filename
    (e.g. current.log.2026-03-01). This namer replaces the full path
    with just the date suffix + .log extension in the same directory.
    """
    directory = os.path.dirname(default_name)
    # default_name is like /path/to/current.log.2026-03-01
    suffix = default_name.rsplit(".", 1)[-1]  # "2026-03-01"
    return os.path.join(directory, f"{suffix}.log")
```

**Step 4: Verify the file is syntactically valid**

Run from project root:
```bash
cd libs/service-commons && uv run python -c "from service_commons.logging import setup_logging; print('OK')"
```
Expected: `OK`

**Step 5: Commit**

```bash
git add libs/service-commons/src/service_commons/logging.py
git commit -m "feat(service-commons): add rotating file logging to setup_logging"
```

---

### Task 2: Update identity service config and lifespan

**Files:**
- Modify: `services/identity/config.yaml`
- Modify: `services/identity/src/identity_service/config.py` (lines 43-48, LoggingConfig class)
- Modify: `services/identity/src/identity_service/core/lifespan.py` (line 26, setup_logging call)

**Step 1: Read the three files**

Read all three files listed above.

**Step 2: Update config.yaml**

Remove `format: "json"`, add `directory: "data/logs"`:

```yaml
logging:
  level: "INFO"
  directory: "data/logs"
```

**Step 3: Update LoggingConfig in config.py**

Replace the LoggingConfig class (lines 43-48):

```python
class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")
    level: str
    directory: str
```

**Step 4: Update lifespan.py**

Change the `setup_logging` call (line 26) to pass the directory:

```python
setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
```

**Step 5: Verify the service starts**

```bash
cd services/identity && just run
```

Check terminal output: should see JSON logs on stdout. Check `services/identity/data/logs/current.log` exists.
Stop the service (Ctrl+C).

**Step 6: Run CI**

```bash
cd services/identity && just ci-quiet
```

Expected: all checks pass.

**Step 7: Commit**

```bash
git add services/identity/config.yaml services/identity/src/identity_service/config.py services/identity/src/identity_service/core/lifespan.py
git commit -m "feat(identity): add file logging config, remove unused format field"
```

---

### Task 3: Update central-bank service config and lifespan

**Files:**
- Modify: `services/central-bank/config.yaml`
- Modify: `services/central-bank/src/central_bank_service/config.py` (lines 43-48)
- Modify: `services/central-bank/src/central_bank_service/core/lifespan.py` (line 27)

**Step 1: Read the three files**

**Step 2: Update config.yaml**

Remove `format: "json"`, add `directory: "data/logs"`:

```yaml
logging:
  level: "INFO"
  directory: "data/logs"
```

**Step 3: Update LoggingConfig in config.py**

Replace the LoggingConfig class (lines 43-48):

```python
class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")
    level: str
    directory: str
```

**Step 4: Update lifespan.py**

Change the `setup_logging` call (line 27) to pass the directory:

```python
setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
```

**Step 5: Run CI**

```bash
cd services/central-bank && just ci-quiet
```

Expected: all checks pass.

**Step 6: Commit**

```bash
git add services/central-bank/config.yaml services/central-bank/src/central_bank_service/config.py services/central-bank/src/central_bank_service/core/lifespan.py
git commit -m "feat(central-bank): add file logging config, remove unused format field"
```

---

### Task 4: Update task-board service config and lifespan

**Files:**
- Modify: `services/task-board/config.yaml`
- Modify: `services/task-board/src/task_board_service/config.py` (lines 43-48)
- Modify: `services/task-board/src/task_board_service/core/lifespan.py` (line 32)

**Step 1: Read the three files**

**Step 2: Update config.yaml**

Remove `format: "json"`, add `directory: "data/logs"`:

```yaml
logging:
  level: "INFO"
  directory: "data/logs"
```

**Step 3: Update LoggingConfig in config.py**

Replace the LoggingConfig class (lines 43-48):

```python
class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")
    level: str
    directory: str
```

**Step 4: Update lifespan.py**

Change the `setup_logging` call (line 32) to pass the directory:

```python
setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
```

**Step 5: Run CI**

```bash
cd services/task-board && just ci-quiet
```

Expected: all checks pass.

**Step 6: Commit**

```bash
git add services/task-board/config.yaml services/task-board/src/task_board_service/config.py services/task-board/src/task_board_service/core/lifespan.py
git commit -m "feat(task-board): add file logging config, remove unused format field"
```

---

### Task 5: Update reputation service config and lifespan

**Files:**
- Modify: `services/reputation/config.yaml`
- Modify: `services/reputation/src/reputation_service/config.py` (lines 43-48)
- Modify: `services/reputation/src/reputation_service/core/lifespan.py` (line 28)

**Step 1: Read the three files**

**Step 2: Update config.yaml**

Remove `format: "json"`, add `directory: "data/logs"`:

```yaml
logging:
  level: "INFO"
  directory: "data/logs"
```

**Step 3: Update LoggingConfig in config.py**

Replace the LoggingConfig class (lines 43-48):

```python
class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")
    level: str
    directory: str
```

**Step 4: Update lifespan.py**

Change the `setup_logging` call (line 28) to pass the directory:

```python
setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
```

**Step 5: Run CI**

```bash
cd services/reputation && just ci-quiet
```

Expected: all checks pass.

**Step 6: Commit**

```bash
git add services/reputation/config.yaml services/reputation/src/reputation_service/config.py services/reputation/src/reputation_service/core/lifespan.py
git commit -m "feat(reputation): add file logging config, remove unused format field"
```

---

### Task 6: Update observatory service config and lifespan

**Files:**
- Modify: `services/observatory/config.yaml`
- Modify: `services/observatory/src/observatory_service/config.py` (lines 43-48)
- Modify: `services/observatory/src/observatory_service/core/lifespan.py` (line 26)

**Step 1: Read the three files**

**Step 2: Update config.yaml**

Remove `format: "json"`, add `directory: "data/logs"`:

```yaml
logging:
  level: "INFO"
  directory: "data/logs"
```

**Step 3: Update LoggingConfig in config.py**

Replace the LoggingConfig class (lines 43-48):

```python
class LoggingConfig(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")
    level: str
    directory: str
```

**Step 4: Update lifespan.py**

Change the `setup_logging` call (line 26) to pass the directory:

```python
setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
```

**Step 5: Run CI**

```bash
cd services/observatory && just ci-quiet
```

Expected: all checks pass.

**Step 6: Commit**

```bash
git add services/observatory/config.yaml services/observatory/src/observatory_service/config.py services/observatory/src/observatory_service/core/lifespan.py
git commit -m "feat(observatory): add file logging config, remove unused format field"
```

---

### Task 7: Update court service config.yaml (no config.py yet)

The court service is not implemented yet — it has no `config.py` module. Only update the `config.yaml` so it's ready when the service is built.

**Files:**
- Modify: `services/court/config.yaml`

**Step 1: Read the file**

**Step 2: Update config.yaml**

Remove `format: "json"`, add `directory: "data/logs"`:

```yaml
logging:
  level: "INFO"
  directory: "data/logs"
```

**Step 3: Commit**

```bash
git add services/court/config.yaml
git commit -m "feat(court): add file logging config, remove unused format field"
```

---

### Task 8: Update per-service logging.py re-exports

Each service's `logging.py` re-exports `setup_logging` from `service_commons`. The signature changed (new `log_directory` param), but since these files just re-export the function reference, no code changes are needed. Verify this.

**Files:**
- Read (verify only): `services/identity/src/identity_service/logging.py`
- Read (verify only): `services/central-bank/src/central_bank_service/logging.py`
- Read (verify only): `services/task-board/src/task_board_service/logging.py`
- Read (verify only): `services/reputation/src/reputation_service/logging.py`
- Read (verify only): `services/observatory/src/observatory_service/logging.py`

**Step 1: Read all five files**

Confirm each file imports `setup_logging` from `service_commons.logging` and re-exports it in `__all__`. No changes needed — the function reference picks up the new signature automatically.

**Step 2: No commit needed**

---

### Task 9: Run full CI across all implemented services

**Step 1: Run CI for all services**

```bash
just ci-all-quiet
```

Expected: all checks pass for identity, central-bank, task-board, reputation, observatory. Court will fail (not implemented — expected).

**Step 2: Fix any issues**

If any service fails CI, read the error output, fix the issue, re-run CI for that service, and commit the fix.

---

### Task 10: Verify file logging works end-to-end

**Step 1: Start identity service**

```bash
cd services/identity && just run
```

**Step 2: Trigger some log output**

In another terminal:
```bash
curl http://localhost:8001/health
```

**Step 3: Verify log file exists**

```bash
ls services/identity/data/logs/
cat services/identity/data/logs/current.log
```

Expected: `current.log` exists and contains JSON log lines. Each line has `timestamp`, `level`, `logger`, `message` fields.

**Step 4: Stop service and commit**

Stop the service. No code changes needed — this is a manual verification step.
