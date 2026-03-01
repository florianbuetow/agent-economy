# Phase 2 — Foundation Files

## Working Directory

```
services/court/
```

---

## File 1: `src/court_service/__init__.py`

Add the module docstring and `__version__` string. Follow the pattern from `central_bank_service/__init__.py`:

```python
"""Court Service — LLM-based dispute resolution for the agent economy."""

__version__ = "0.1.0"
```

---

## File 2: `src/court_service/config.py`

Pydantic settings that load `config.yaml` with zero defaults. Follow the exact pattern from `central_bank_service/config.py`.

### Config Sections (each a `BaseModel` with `extra="forbid"`)

| Class | Fields |
|-------|--------|
| `ServiceConfig` | `name: str`, `version: str` |
| `ServerConfig` | `host: str`, `port: int`, `log_level: str` |
| `LoggingConfig` | `level: str`, `format: str` |
| `DatabaseConfig` | `path: str` |
| `IdentityConfig` | `base_url: str`, `verify_jws_path: str` |
| `TaskBoardConfig` | `base_url: str` |
| `CentralBankConfig` | `base_url: str` |
| `ReputationConfig` | `base_url: str` |
| `PlatformConfig` | `agent_id: str`, `private_key_path: str` |
| `DisputesConfig` | `rebuttal_deadline_seconds: int` |
| `JudgeConfig` | `id: str`, `model: str`, `temperature: float` |
| `JudgesConfig` | `panel_size: int`, `judges: list[JudgeConfig]` |
| `RequestConfig` | `max_body_size: int` |
| `Settings` | All of the above as required fields |

### Validators

Add `field_validator` on `PlatformConfig`:
- `agent_id` must not be empty (same as Central Bank)
- `private_key_path` must not be empty

Add `model_validator` on `JudgesConfig` (mode `"after"`):
- `panel_size` must be odd and >= 1
- `panel_size` must equal `len(judges)`
- All judge IDs must be unique

These validators cause the service to crash at startup with a clear error if misconfigured. This is intentional — fail fast.

### Functions

Same pattern as Central Bank:
- `get_config_path()` — resolves via `CONFIG_PATH` env var, default filename `"config.yaml"`
- `get_settings, clear_settings_cache` — from `create_settings_loader`
- `get_safe_config()` — redacted config for logging

---

## File 3: `src/court_service/logging.py`

Identical pattern to `central_bank_service/logging.py`. Re-exports from `service_commons.logging`, defines `get_logger(name)` that prefixes with the service name from config.

---

## File 4: `src/court_service/schemas.py`

Pydantic response models. All models use `ConfigDict(extra="forbid")`.

### Models

**`HealthResponse`**:
- `status: Literal["ok"]`
- `uptime_seconds: float`
- `started_at: str`
- `total_disputes: int`
- `active_disputes: int` — disputes NOT in `"ruled"` status

**`ErrorResponse`**:
- `error: str`
- `message: str`
- `details: dict[str, object]`

**`VoteResponse`**:
- `vote_id: str`
- `dispute_id: str`
- `judge_id: str`
- `worker_pct: int`
- `reasoning: str`
- `voted_at: str`

**`DisputeResponse`** (full detail, returned by GET /disputes/{id} and all POST endpoints):
- `dispute_id: str`
- `task_id: str`
- `claimant_id: str`
- `respondent_id: str`
- `claim: str`
- `rebuttal: str | None`
- `status: str`
- `rebuttal_deadline: str`
- `worker_pct: int | None`
- `ruling_summary: str | None`
- `escrow_id: str`
- `filed_at: str`
- `rebutted_at: str | None`
- `ruled_at: str | None`
- `votes: list[VoteResponse]`

**`DisputeSummary`** (list view, returned by GET /disputes):
- `dispute_id: str`
- `task_id: str`
- `claimant_id: str`
- `respondent_id: str`
- `status: str`
- `worker_pct: int | None`
- `filed_at: str`
- `ruled_at: str | None`

**`DisputeListResponse`**:
- `disputes: list[DisputeSummary]`

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from court_service.config import Settings; print('config OK')"
uv run python -c "from court_service.schemas import DisputeResponse; print('schemas OK')"
```
