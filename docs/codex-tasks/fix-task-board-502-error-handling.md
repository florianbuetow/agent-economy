# Fix Task Board 502 Error Handling for Missing Bank Accounts

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> Do NOT modify any existing test files in tests/.
> After EACH task, run: `cd services/task-board && just test`
> Skip `just ci-quiet` — I will run it on my machine. Just run `just test`.
> Commit after each task with a descriptive message.

## Background

The Task Board's `CentralBankClient` (`services/task-board/src/task_board_service/clients/central_bank_client.py`) has three methods that call the Central Bank for escrow operations:

- `lock_escrow` — forwards a poster-signed token to `POST /escrow/lock`
- `release_escrow` — sends a platform-signed token to `POST /escrow/{id}/release`
- `split_escrow` — sends a platform-signed token to `POST /escrow/{id}/split`

All three methods only handle specific success codes (201 or 200) and, for `lock_escrow` only, 402 INSUFFICIENT_FUNDS. **Every other response** — including 404 ACCOUNT_NOT_FOUND — falls through to a catch-all that raises `ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502)`.

This means when the Central Bank returns 404 because an agent has no bank account, the Task Board returns 502 Bad Gateway to the caller. The correct behavior is to propagate the 404 with a meaningful error like `ACCOUNT_NOT_FOUND`.

The Central Bank can return these 4xx errors that should be propagated, not masked:

- **404 ACCOUNT_NOT_FOUND** — the agent's bank account doesn't exist
- **404 ESCROW_NOT_FOUND** — the escrow record doesn't exist
- **403 FORBIDDEN** — authorization failure (e.g., wrong signer)
- **400 INVALID_PAYLOAD** — malformed JWS payload
- **409 ESCROW_ALREADY_RELEASED** — escrow was already released

## Important Files to Read First

1. `AGENTS.md` — project conventions
2. `services/task-board/src/task_board_service/clients/central_bank_client.py` — the file to modify
3. `services/task-board/tests/unit/test_escrow_coordinator.py` — existing escrow tests for reference patterns
4. `services/task-board/tests/unit/routers/conftest.py` — test fixtures (mock setup patterns)

---

## Task 1: Add 4xx error propagation to `lock_escrow`

**File to modify:** `services/task-board/src/task_board_service/clients/central_bank_client.py`

**Steps:**

1. In the `lock_escrow` method, after the existing `if response.status_code == 402:` block (around line 100-107), add a new block to handle 4xx responses before the catch-all:

```python
        if response.status_code == 404:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "ACCOUNT_NOT_FOUND"),
                message=error_body.get("message", "Account not found in Central Bank"),
                status_code=404,
                details=error_body.get("details", {}),
            )

        if response.status_code == 403:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "FORBIDDEN"),
                message=error_body.get("message", "Central Bank authorization failed"),
                status_code=403,
                details=error_body.get("details", {}),
            )

        if response.status_code == 409:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "CONFLICT"),
                message=error_body.get("message", "Central Bank conflict"),
                status_code=409,
                details=error_body.get("details", {}),
            )
```

Place these AFTER the 402 block and BEFORE the catch-all `logger.warning` + `raise ServiceError(...502...)`.

2. Also update the docstring of `lock_escrow` to mention the new raised errors:

```python
        Raises:
            ServiceError: INSUFFICIENT_FUNDS (402) if the poster cannot cover the reward
            ServiceError: ACCOUNT_NOT_FOUND (404) if the poster has no bank account
            ServiceError: FORBIDDEN (403) if authorization failed
            ServiceError: CENTRAL_BANK_UNAVAILABLE (502) on connection/timeout/unexpected errors
```

3. Run `cd services/task-board && just test` — all existing tests should still pass.

4. Commit: `fix(task-board): propagate 404/403/409 from central bank in lock_escrow`

---

## Task 2: Add 4xx error propagation to `release_escrow`

**File to modify:** `services/task-board/src/task_board_service/clients/central_bank_client.py`

**Steps:**

1. In the `release_escrow` method, after the `if response.status_code == 200:` block (around line 200-202), add the same 4xx handling blocks BEFORE the catch-all:

```python
        if response.status_code == 404:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "NOT_FOUND"),
                message=error_body.get("message", "Resource not found in Central Bank"),
                status_code=404,
                details=error_body.get("details", {}),
            )

        if response.status_code == 403:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "FORBIDDEN"),
                message=error_body.get("message", "Central Bank authorization failed"),
                status_code=403,
                details=error_body.get("details", {}),
            )

        if response.status_code == 409:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "CONFLICT"),
                message=error_body.get("message", "Central Bank conflict"),
                status_code=409,
                details=error_body.get("details", {}),
            )
```

2. Update the docstring of `release_escrow` similarly.

3. Run `cd services/task-board && just test` — all existing tests should still pass.

4. Commit: `fix(task-board): propagate 4xx errors from central bank in release_escrow`

---

## Task 3: Add 4xx error propagation to `split_escrow`

**File to modify:** `services/task-board/src/task_board_service/clients/central_bank_client.py`

**Steps:**

1. In the `split_escrow` method, after the `if response.status_code == 200:` block (around line 293-295), add the same 4xx handling blocks BEFORE the catch-all:

```python
        if response.status_code == 404:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "NOT_FOUND"),
                message=error_body.get("message", "Resource not found in Central Bank"),
                status_code=404,
                details=error_body.get("details", {}),
            )

        if response.status_code == 403:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "FORBIDDEN"),
                message=error_body.get("message", "Central Bank authorization failed"),
                status_code=403,
                details=error_body.get("details", {}),
            )

        if response.status_code == 409:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "CONFLICT"),
                message=error_body.get("message", "Central Bank conflict"),
                status_code=409,
                details=error_body.get("details", {}),
            )
```

2. Update the docstring of `split_escrow` similarly.

3. Run `cd services/task-board && just test` — all existing tests should still pass.

4. Commit: `fix(task-board): propagate 4xx errors from central bank in split_escrow`

---

## Task 4: Add unit tests for the new error handling

**File to create:** `services/task-board/tests/unit/clients/test_central_bank_client_errors.py`

Do NOT modify any existing test files.

**Steps:**

1. Create the directory if needed: `services/task-board/tests/unit/clients/`
2. Create an `__init__.py` in that directory if it doesn't exist.
3. Create the test file with these tests, all marked with `@pytest.mark.unit`:

The tests should instantiate `CentralBankClient` directly with a mock HTTP client (use `httpx.MockTransport` or monkeypatch `self._client.post`). For the platform signer, create a simple mock that returns a dummy token string.

**Test setup pattern:**

```python
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from service_commons.exceptions import ServiceError

from task_board_service.clients.central_bank_client import CentralBankClient


def _make_client(mock_response: httpx.Response) -> CentralBankClient:
    """Create a CentralBankClient with a mock HTTP transport."""
    mock_signer = MagicMock()
    mock_signer.sign.return_value = "mock-jws-token"

    client = CentralBankClient(
        base_url="http://mock-bank:8002",
        escrow_lock_path="/escrow/lock",
        escrow_release_path="/escrow/{escrow_id}/release",
        escrow_split_path="/escrow/{escrow_id}/split",
        timeout_seconds=5,
        platform_signer=mock_signer,
    )
    # Replace internal client with a mock
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(return_value=mock_response)
    client._client = mock_http
    return client


def _mock_response(status_code: int, json_body: dict[str, Any]) -> httpx.Response:
    """Create a mock httpx.Response."""
    response = httpx.Response(
        status_code=status_code,
        json=json_body,
        request=httpx.Request("POST", "http://mock-bank:8002/escrow/lock"),
    )
    return response
```

**Tests:**

```
TestLockEscrowErrorPropagation:
    test_lock_escrow_404_raises_account_not_found:
        - Mock response: 404 {"error": "ACCOUNT_NOT_FOUND", "message": "Account not found", "details": {}}
        - Call lock_escrow("fake-token")
        - Assert raises ServiceError with status_code=404, error="ACCOUNT_NOT_FOUND"

    test_lock_escrow_403_raises_forbidden:
        - Mock response: 403 {"error": "FORBIDDEN", "message": "Not authorized", "details": {}}
        - Call lock_escrow("fake-token")
        - Assert raises ServiceError with status_code=403, error="FORBIDDEN"

    test_lock_escrow_409_raises_conflict:
        - Mock response: 409 {"error": "ESCROW_ALREADY_EXISTS", "message": "...", "details": {}}
        - Call lock_escrow("fake-token")
        - Assert raises ServiceError with status_code=409, error="ESCROW_ALREADY_EXISTS"

    test_lock_escrow_500_still_raises_502:
        - Mock response: 500 {"error": "INTERNAL_ERROR", "message": "...", "details": {}}
        - Call lock_escrow("fake-token")
        - Assert raises ServiceError with status_code=502, error="CENTRAL_BANK_UNAVAILABLE"

TestReleaseEscrowErrorPropagation:
    test_release_escrow_404_raises_not_found:
        - Mock response: 404 {"error": "ESCROW_NOT_FOUND", "message": "...", "details": {}}
        - Call release_escrow("esc-fake", "a-recipient")
        - Assert raises ServiceError with status_code=404, error="ESCROW_NOT_FOUND"

    test_release_escrow_403_raises_forbidden:
        - Mock response: 403 {"error": "FORBIDDEN", "message": "...", "details": {}}
        - Call release_escrow("esc-fake", "a-recipient")
        - Assert raises ServiceError with status_code=403, error="FORBIDDEN"

TestSplitEscrowErrorPropagation:
    test_split_escrow_404_raises_not_found:
        - Mock response: 404 {"error": "ACCOUNT_NOT_FOUND", "message": "...", "details": {}}
        - Call split_escrow("esc-fake", "a-worker", "a-poster", 70)
        - Assert raises ServiceError with status_code=404, error="ACCOUNT_NOT_FOUND"

    test_split_escrow_403_raises_forbidden:
        - Mock response: 403 {"error": "FORBIDDEN", "message": "...", "details": {}}
        - Call split_escrow("esc-fake", "a-worker", "a-poster", 70)
        - Assert raises ServiceError with status_code=403, error="FORBIDDEN"
```

4. Run `cd services/task-board && just test` — all tests should pass.

5. Commit: `test(task-board): add unit tests for central bank client error propagation`

---

## Summary

Execute tasks 1 through 4 in order. Each task builds on the previous.
After each task, run `cd services/task-board && just test`.
Do NOT run `just ci-quiet` — I will run it myself.
