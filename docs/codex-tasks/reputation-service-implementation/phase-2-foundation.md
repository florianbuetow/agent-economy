# Phase 2 — Foundation Files

## Working Directory

```
services/reputation/
```

---

## File 1: `src/reputation_service/__init__.py`

Add the module docstring and `__version__` string. Follow the pattern from `central_bank_service/__init__.py`:

```python
"""Reputation Service — bidirectional feedback with visibility sealing."""

__version__ = "0.1.0"
```

---

## File 2: `src/reputation_service/config.py`

Pydantic settings that load `config.yaml` with zero defaults. Follow the exact pattern from `central_bank_service/config.py`.

### Config Sections (each a `BaseModel` with `extra="forbid"`)

| Class | Fields |
|-------|--------|
| `ServiceConfig` | `name: str`, `version: str` |
| `ServerConfig` | `host: str`, `port: int`, `log_level: str` |
| `LoggingConfig` | `level: str`, `format: str` |
| `IdentityConfig` | `base_url: str`, `verify_jws_path: str`, `timeout_seconds: int` |
| `RequestConfig` | `max_body_size: int` |
| `DatabaseConfig` | `path: str` |
| `FeedbackConfig` | `reveal_timeout_seconds: int`, `max_comment_length: int` |
| `Settings` | All of the above as required fields |

### Key Differences from Central Bank

- No `PlatformConfig` — no platform signing needed
- No `CentralBankConfig`, `TaskBoardConfig`, or `ReputationConfig` — only talks to Identity
- Has `FeedbackConfig` with domain-specific settings
- `IdentityConfig` includes `timeout_seconds` (Central Bank hardcodes timeout in the client constructor)

### No Validators

Unlike the Court service (which validates judge panel size) or the Central Bank (which validates platform agent_id), the Reputation service has no custom validators. All config sections are simple required-field models.

### Functions

Same pattern as Central Bank:
- `get_config_path()` — resolves via `CONFIG_PATH` env var, default filename `"config.yaml"`
- `get_settings, clear_settings_cache` — from `create_settings_loader`
- `get_safe_config()` — redacted config for logging

---

## File 3: `src/reputation_service/logging.py`

Identical pattern to `central_bank_service/logging.py`. Re-exports from `service_commons.logging`, defines `get_logger(name)` that uses lazy import to avoid circular dependencies:

```python
def get_logger(name: str) -> logging.Logger:
    from reputation_service.config import get_settings  # noqa: PLC0415
    settings = get_settings()
    return get_named_logger(settings.service.name, name)
```

---

## File 4: `src/reputation_service/schemas.py`

Pydantic response models. All models use `ConfigDict(extra="forbid")`.

### Models

**`HealthResponse`**:
- `status: Literal["ok"]`
- `uptime_seconds: float`
- `started_at: str`
- `total_feedback: int`

**`ErrorResponse`**:
- `error: str`
- `message: str`
- `details: dict[str, Any]`

**`FeedbackResponse`**:
- `feedback_id: str`
- `task_id: str`
- `from_agent_id: str`
- `to_agent_id: str`
- `category: str`
- `rating: str`
- `comment: str | None`
- `submitted_at: str`
- `visible: bool`

**`TaskFeedbackResponse`**:
- `task_id: str`
- `feedback: list[FeedbackResponse]`

**`AgentFeedbackResponse`**:
- `agent_id: str`
- `feedback: list[FeedbackResponse]`

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from reputation_service.config import Settings; print('config OK')"
uv run python -c "from reputation_service.schemas import FeedbackResponse; print('schemas OK')"
```
