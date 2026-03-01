# Phase 5 — Business Logic Service

## Working Directory

```
services/reputation/
```

---

## File 1: `src/reputation_service/services/feedback.py`

Pure Python business logic — no FastAPI imports. This module owns all validation rules and feedback operations.

### Constants

```python
VALID_CATEGORIES: frozenset[str] = frozenset({"spec_quality", "delivery_quality"})
VALID_RATINGS: frozenset[str] = frozenset({"dissatisfied", "satisfied", "extremely_satisfied"})
REQUIRED_FIELDS: list[str] = ["task_id", "from_agent_id", "to_agent_id", "category", "rating"]
```

### `ValidationError` Dataclass

```python
@dataclass
class ValidationError:
    error: str
    message: str
    status_code: int
    details: dict[str, object]
```

This is a domain result type, not an exception. The router converts it to a `ServiceError` raise.

### `validate_feedback(body, max_comment_length)` → `ValidationError | None`

Validation order (first match wins):

1. **INVALID_FIELD_TYPE** — required fields that are present but not strings (int, bool, list, dict)
2. **MISSING_FIELD** — required fields that are absent, `None`, or empty string `""`
3. **SELF_FEEDBACK** — `from_agent_id == to_agent_id`
4. **INVALID_CATEGORY** — not in `VALID_CATEGORIES`
5. **INVALID_RATING** — not in `VALID_RATINGS`
6. **COMMENT_TOO_LONG** — comment is a string and `len(comment) > max_comment_length` (Unicode codepoints)

Returns `None` if all validations pass.

### `submit_feedback(store, body, max_comment_length)` → `FeedbackRecord | ValidationError`

1. Call `validate_feedback()` — return `ValidationError` if invalid
2. Extract `task_id`, `from_agent_id`, `to_agent_id`, `category`, `rating` as strings
3. Extract `comment`: if `None` → `None`, if string → string as-is, if other type → `None`
4. Call `store.insert_feedback(...)` — if `DuplicateFeedbackError`, return `ValidationError("FEEDBACK_EXISTS", ..., 409)`
5. Return the `FeedbackRecord` on success

### `is_visible(record, reveal_timeout_seconds)` → `bool`

Lazy visibility check:
- If `record.visible` is `True` → visible (mutual reveal already happened)
- Otherwise: parse `record.submitted_at`, check if elapsed >= `reveal_timeout_seconds`

### Query Functions

All three filter by visibility:

- `get_feedback_by_id(store, feedback_id, reveal_timeout_seconds)` → `FeedbackRecord | None`
  - Returns `None` if not found OR if sealed (not yet visible)
- `get_feedback_for_task(store, task_id, reveal_timeout_seconds)` → `list[FeedbackRecord]`
  - Filters to only visible records
- `get_feedback_for_agent(store, agent_id, reveal_timeout_seconds)` → `list[FeedbackRecord]`
  - Filters to only visible records (where `to_agent_id` matches)

---

## File 2: `src/reputation_service/services/__init__.py`

Export the public API:

```python
from reputation_service.services.feedback import (
    get_feedback_by_id,
    get_feedback_for_agent,
    get_feedback_for_task,
    submit_feedback,
    validate_feedback,
)
```

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from reputation_service.services.feedback import validate_feedback, submit_feedback; print('service OK')"
```
