# Rotating File Logging — Design

## Problem

All 6 services log exclusively to stdout via `service_commons.logging.setup_logging()`. There is no file-based log persistence. Logs are lost when processes restart.

## Decision

Add daily rotating file logging to `service-commons`, configured per service via `config.yaml`.

## Design

### Config

Each service's `config.yaml` logging section becomes:

```yaml
logging:
  level: "INFO"
  directory: "data/logs"
```

- `format` field is removed — JSON is the only format, hardcoded in `service_commons`.
- `directory` is required — specifies where log files are written.
- No default values, no fallback values.

### File naming

Log files use `YYYY-MM-DD.log` naming via `TimedRotatingFileHandler` with `when="midnight"`.

### Log targets

Both stdout and file are always active. Stdout uses `StreamHandler(sys.stdout)`. File uses `TimedRotatingFileHandler`. Both use the same `JSONFormatter`.

### Retention

No auto-deletion. All log files are kept indefinitely.

### Directory creation

`setup_logging()` creates the log directory if it doesn't exist (`os.makedirs(directory, exist_ok=True)`).

### Gitignore

`services/*/data/` is already gitignored. No changes needed.

## Changes

1. **`libs/service-commons/src/service_commons/logging.py`** — add `log_directory: str` param to `setup_logging()`, add `TimedRotatingFileHandler`, remove `format` references.
2. **6x `config.yaml`** — remove `format: "json"`, add `directory: "data/logs"`.
3. **6x config model classes** — remove `format` field, add `directory: str`.
4. **6x `lifespan.py`** — pass `settings.logging.directory` to `setup_logging()`.
