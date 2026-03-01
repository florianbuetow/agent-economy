# Court Service Acceptance Tests Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Write 108 acceptance tests (75 core + 33 auth) for the Court service that are CI-compliant but expected to fail at runtime.

**Architecture:** Tests follow the project's unit test pattern: `conftest.py` provides app/client fixtures with mocked external services (Identity, Task Board, Central Bank, Reputation, Judge). Tests use fake JWS tokens (base64-encoded, no real crypto) and AsyncMock-based service mocks. All tests are in `services/court/tests/unit/`.

**Tech Stack:** pytest, pytest-asyncio, httpx (ASGITransport + AsyncClient), unittest.mock (AsyncMock), FastAPI test client pattern

**Source of truth:**
- `docs/specifications/service-tests/court-service-tests.md` — 75 test cases
- `docs/specifications/service-tests/court-service-auth-tests.md` — 33 test cases
- `docs/specifications/service-api/court-service-specs.md` — API spec
- `docs/specifications/service-api/court-service-auth-specs.md` — Auth spec

**Reference implementations:**
- `services/reputation/tests/helpers.py` — fake JWS + mock identity pattern
- `services/reputation/tests/unit/routers/conftest.py` — app/client fixture pattern
- `services/central-bank/tests/unit/routers/conftest.py` — alternative fixture pattern

---

## Important Notes for Implementation

### Court service is NOT implemented yet

The service has only empty scaffolding (`__init__.py` files in `src/court_service/`, `core/`, `routers/`, `services/`). There is no `create_app`, no `config.py`, no `lifespan.py`, no routers.

**Therefore:** Tests will import from `court_service.*` modules that don't exist. This means:
- Tests will fail with `ImportError` at runtime — this is expected and correct
- `ruff check` and `ruff format` will pass (they don't resolve imports)
- `mypy` only checks `src/` (configured in pyproject.toml), so test imports won't cause mypy failures
- `pyright` only checks `src/` (configured in pyrightconfig.json), so test imports won't cause pyright failures
- `codespell`, `bandit`, `semgrep` will pass on valid Python files

### Fake JWS pattern (not real crypto)

The court service `pyproject.toml` does NOT include `joserfc` or `cryptography`. Use the reputation service's fake JWS approach:
- Base64-encode a JSON header and payload, append a fake signature
- Mock the Identity client to return `valid: True` with the decoded payload
- No actual Ed25519 signing needed

### Test IDs in docstrings

Every test method must have a docstring starting with the spec test ID (e.g., `"""FILE-01: File a valid dispute."""`).

### All tests marked `@pytest.mark.unit`

The test spec says these are unit tests (mocked deps, no running services).

---

## Task 1: Create test helpers (`tests/helpers.py`)

**Files:**
- Create: `services/court/tests/helpers.py`

**Step 1: Write helpers**

Create shared JWS and mock helpers following the reputation service pattern.

```python
"""Shared test helpers for Court service tests."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock


def make_jws_token(payload: dict[str, Any], kid: str = "a-platform-test") -> str:
    """Build a fake but structurally valid JWS compact serialization."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "kid": kid}).encode())
        .rstrip(b"=")
        .decode()
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(b"fake-signature").rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


def make_tampered_jws(payload: dict[str, Any], kid: str = "a-platform-test") -> str:
    """Build a JWS with a modified payload (signature won't match)."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "kid": kid}).encode())
        .rstrip(b"=")
        .decode()
    )
    # Encode original payload then tamper by using different payload bytes
    original = json.dumps(payload).encode()
    tampered = json.dumps({**payload, "_tampered": True}).encode()
    body = base64.urlsafe_b64encode(tampered).rstrip(b"=").decode()
    # Signature was computed over original, not tampered
    signature = base64.urlsafe_b64encode(b"sig-over-" + original[:20]).rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


def make_mock_identity_client(
    verify_response: dict[str, Any] | None = None,
    verify_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock IdentityClient returning predictable responses."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    if verify_side_effect is not None:
        mock_client.verify_jws.side_effect = verify_side_effect
    elif verify_response is not None:
        mock_client.verify_jws.return_value = verify_response
    return mock_client


def make_mock_task_board_client(
    task_response: dict[str, Any] | None = None,
    task_side_effect: Exception | None = None,
    record_ruling_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock TaskBoardClient."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    if task_side_effect is not None:
        mock_client.get_task.side_effect = task_side_effect
    elif task_response is not None:
        mock_client.get_task.return_value = task_response
    if record_ruling_side_effect is not None:
        mock_client.record_ruling.side_effect = record_ruling_side_effect
    else:
        mock_client.record_ruling.return_value = {"status": "ok"}
    return mock_client


def make_mock_central_bank_client(
    split_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock CentralBankClient."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    if split_side_effect is not None:
        mock_client.split_escrow.side_effect = split_side_effect
    else:
        mock_client.split_escrow.return_value = {"status": "ok"}
    return mock_client


def make_mock_reputation_client(
    feedback_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock ReputationClient."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    if feedback_side_effect is not None:
        mock_client.record_feedback.side_effect = feedback_side_effect
    else:
        mock_client.record_feedback.return_value = {"status": "ok"}
    return mock_client


def make_mock_judge(
    worker_pct: int = 70,
    reasoning: str = "Test reasoning for the ruling.",
    side_effect: Exception | None = None,
) -> AsyncMock:
    """Create a mock Judge that returns a predictable vote."""
    mock_judge = AsyncMock()
    if side_effect is not None:
        mock_judge.evaluate.side_effect = side_effect
    else:
        mock_judge.evaluate.return_value = {
            "worker_pct": worker_pct,
            "reasoning": reasoning,
        }
    return mock_judge


def new_task_id() -> str:
    """Generate a random task ID."""
    return f"t-{uuid.uuid4()}"


def new_agent_id() -> str:
    """Generate a random agent ID."""
    return f"a-{uuid.uuid4()}"


def new_escrow_id() -> str:
    """Generate a random escrow ID."""
    return f"esc-{uuid.uuid4()}"


def make_task_data(task_id: str | None = None) -> dict[str, Any]:
    """Create a valid task data response from the Task Board mock."""
    return {
        "task_id": task_id or new_task_id(),
        "title": "Implement email validation",
        "spec": "Build a login page with email validation.",
        "deliverables": "Login page with email field.",
        "reward": 1000,
        "status": "disputed",
    }
```

**Step 2: Verify syntax**

Run: `cd services/court && uv run ruff check tests/helpers.py && uv run ruff format --check tests/helpers.py`
Expected: No errors

**Step 3: Commit**

```bash
git add services/court/tests/helpers.py
git commit -m "test(court): add shared test helpers for JWS, mocks, and ID generators"
```

---

## Task 2: Create conftest fixtures

**Files:**
- Create: `services/court/tests/conftest.py` (replace empty)
- Create: `services/court/tests/unit/conftest.py`
- Create: `services/court/tests/unit/routers/__init__.py`
- Create: `services/court/tests/unit/routers/conftest.py`

**Step 1: Write `tests/conftest.py`**

```python
"""Shared test configuration."""
```

**Step 2: Write `tests/unit/conftest.py`**

```python
"""Unit test fixtures."""

from __future__ import annotations

import os

import pytest

from court_service.config import clear_settings_cache
from court_service.core.state import reset_app_state


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear settings cache and app state between tests."""
    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()
    os.environ.pop("CONFIG_PATH", None)
```

**Step 3: Create `tests/unit/routers/__init__.py`**

Empty file (package marker).

**Step 4: Write `tests/unit/routers/conftest.py`**

This is the key fixture file — it sets up the app, client, and all mocked external dependencies.

```python
"""Router test fixtures with mocked external services."""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient

from court_service.app import create_app
from court_service.config import clear_settings_cache
from court_service.core.state import get_app_state, reset_app_state
from tests.helpers import (
    make_jws_token,
    make_mock_central_bank_client,
    make_mock_identity_client,
    make_mock_judge,
    make_mock_reputation_client,
    make_mock_task_board_client,
    make_task_data,
    new_agent_id,
    new_escrow_id,
    new_task_id,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

PLATFORM_AGENT_ID = "a-platform-test-id"
ROGUE_AGENT_ID = "a-rogue-test-id"
CLAIMANT_ID = "a-claimant-test-id"
RESPONDENT_ID = "a-respondent-test-id"


def _valid_config(tmp_path: Any, db_path: str | None = None) -> str:
    """Write a valid court config.yaml and return its path."""
    if db_path is None:
        db_path = str(tmp_path / "test.db")
    config_content = f"""\
service:
  name: "court"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8005
  log_level: "info"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "{db_path}"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
request:
  max_body_size: 1048576
disputes:
  rebuttal_deadline_seconds: 86400
  max_claim_length: 10000
  max_rebuttal_length: 10000
judges:
  panel_size: 1
  judges:
    - id: "judge-0"
      provider: "mock"
      model: "test-model"
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return str(config_path)


@pytest.fixture
async def app(tmp_path: Any) -> AsyncIterator[FastAPI]:
    """Create a test app with mocked external services."""
    config_path = _valid_config(tmp_path)
    os.environ["CONFIG_PATH"] = config_path

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with test_app.router.lifespan_context(test_app):
        state = get_app_state()
        # Inject mock external services
        state.identity_client = make_mock_identity_client(
            verify_response={
                "valid": True,
                "agent_id": PLATFORM_AGENT_ID,
                "payload": {},
            }
        )
        state.task_board_client = make_mock_task_board_client(
            task_response=make_task_data()
        )
        state.central_bank_client = make_mock_central_bank_client()
        state.reputation_client = make_mock_reputation_client()
        state.judges = [make_mock_judge()]
        yield test_app

    reset_app_state()
    clear_settings_cache()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP client for the test app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


def file_dispute_payload(
    task_id: str | None = None,
    claimant_id: str | None = None,
    respondent_id: str | None = None,
    claim: str = "The worker did not deliver as specified.",
    escrow_id: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Return a valid file_dispute JWS payload."""
    base: dict[str, Any] = {
        "action": "file_dispute",
        "task_id": task_id or new_task_id(),
        "claimant_id": claimant_id or CLAIMANT_ID,
        "respondent_id": respondent_id or RESPONDENT_ID,
        "claim": claim,
        "escrow_id": escrow_id or new_escrow_id(),
    }
    base.update(overrides)
    return base


def rebuttal_payload(
    dispute_id: str,
    rebuttal: str = "The specification was ambiguous.",
    **overrides: Any,
) -> dict[str, Any]:
    """Return a valid submit_rebuttal JWS payload."""
    base: dict[str, Any] = {
        "action": "submit_rebuttal",
        "dispute_id": dispute_id,
        "rebuttal": rebuttal,
    }
    base.update(overrides)
    return base


def ruling_payload(dispute_id: str, **overrides: Any) -> dict[str, Any]:
    """Return a valid trigger_ruling JWS payload."""
    base: dict[str, Any] = {
        "action": "trigger_ruling",
        "dispute_id": dispute_id,
    }
    base.update(overrides)
    return base


def token_body(payload: dict[str, Any], kid: str = PLATFORM_AGENT_ID) -> dict[str, str]:
    """Wrap a payload into a {token: ...} body."""
    return {"token": make_jws_token(payload, kid=kid)}


def inject_identity_verify(
    agent_id: str,
    payload: dict[str, Any],
    valid: bool = True,
) -> None:
    """Update the mock identity client to return a specific verify response."""
    state = get_app_state()
    state.identity_client = make_mock_identity_client(
        verify_response={"valid": valid, "agent_id": agent_id, "payload": payload}
    )


def inject_identity_error(error: Exception) -> None:
    """Update the mock identity client to raise an error."""
    state = get_app_state()
    state.identity_client = make_mock_identity_client(verify_side_effect=error)


def inject_task_board_response(task_data: dict[str, Any] | None = None) -> None:
    """Update the mock task board client response."""
    state = get_app_state()
    if task_data is None:
        task_data = make_task_data()
    state.task_board_client = make_mock_task_board_client(task_response=task_data)


def inject_task_board_error(error: Exception) -> None:
    """Update the mock task board client to raise an error."""
    state = get_app_state()
    state.task_board_client = make_mock_task_board_client(task_side_effect=error)


def inject_judge(
    worker_pct: int = 70,
    reasoning: str = "Test reasoning.",
    side_effect: Exception | None = None,
) -> None:
    """Update the mock judge."""
    state = get_app_state()
    state.judges = [make_mock_judge(worker_pct=worker_pct, reasoning=reasoning, side_effect=side_effect)]


def inject_central_bank_error(error: Exception) -> None:
    """Update the mock central bank client to raise an error."""
    state = get_app_state()
    state.central_bank_client = make_mock_central_bank_client(split_side_effect=error)


def inject_reputation_error(error: Exception) -> None:
    """Update the mock reputation client to raise an error."""
    state = get_app_state()
    state.reputation_client = make_mock_reputation_client(feedback_side_effect=error)


async def file_dispute(
    client: AsyncClient,
    payload: dict[str, Any] | None = None,
    kid: str = PLATFORM_AGENT_ID,
) -> dict[str, Any]:
    """File a dispute and return the response JSON. Assumes mocks are set up."""
    if payload is None:
        payload = file_dispute_payload()
    inject_identity_verify(kid, payload)
    response = await client.post("/disputes/file", json=token_body(payload, kid=kid))
    assert response.status_code == 201, f"Failed to file dispute: {response.text}"
    return response.json()


async def file_and_rebut(
    client: AsyncClient,
    file_payload: dict[str, Any] | None = None,
    rebuttal_text: str = "The specification was ambiguous.",
    kid: str = PLATFORM_AGENT_ID,
) -> dict[str, Any]:
    """File a dispute, submit a rebuttal, return the rebuttal response JSON."""
    dispute = await file_dispute(client, payload=file_payload, kid=kid)
    dispute_id = dispute["dispute_id"]
    reb_payload = rebuttal_payload(dispute_id, rebuttal=rebuttal_text)
    inject_identity_verify(kid, reb_payload)
    response = await client.post(
        f"/disputes/{dispute_id}/rebuttal",
        json=token_body(reb_payload, kid=kid),
    )
    assert response.status_code == 200, f"Failed to submit rebuttal: {response.text}"
    return dispute


async def file_rebut_and_rule(
    client: AsyncClient,
    file_payload: dict[str, Any] | None = None,
    rebuttal_text: str = "The specification was ambiguous.",
    worker_pct: int = 70,
    kid: str = PLATFORM_AGENT_ID,
) -> dict[str, Any]:
    """File, rebut, and rule a dispute. Returns the ruling response JSON."""
    dispute = await file_and_rebut(client, file_payload, rebuttal_text, kid)
    dispute_id = dispute["dispute_id"]
    inject_judge(worker_pct=worker_pct)
    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(kid, rule_pay)
    response = await client.post(
        f"/disputes/{dispute_id}/rule",
        json=token_body(rule_pay, kid=kid),
    )
    assert response.status_code == 200, f"Failed to trigger ruling: {response.text}"
    return response.json()
```

**Step 5: Verify syntax**

Run: `cd services/court && uv run ruff check tests/ && uv run ruff format --check tests/`
Expected: Pass (ruff doesn't resolve imports)

**Step 6: Commit**

```bash
git add services/court/tests/conftest.py services/court/tests/unit/conftest.py services/court/tests/unit/routers/__init__.py services/court/tests/unit/routers/conftest.py
git commit -m "test(court): add conftest fixtures with mocked external services"
```

---

## Task 3: Write config tests (`test_config.py`)

**Files:**
- Create: `services/court/tests/unit/test_config.py`

**Test IDs:** JUDGE-01 through JUDGE-05 (5 tests)

These test startup validation of judge panel configuration. They construct `Settings` objects directly and assert validation errors for invalid configs.

```python
"""Configuration and judge panel validation tests.

Covers: JUDGE-01 to JUDGE-05 from court-service-tests.md.
"""

from __future__ import annotations

import os

import pytest

from court_service.config import Settings, clear_settings_cache, get_settings


def _write_config(tmp_path, panel_size: int, judge_count: int) -> str:
    """Write a config with the given panel_size and judge count."""
    judges_yaml = ""
    for i in range(judge_count):
        judges_yaml += f"""
    - id: "judge-{i}"
      provider: "mock"
      model: "test-model"
"""
    config_content = f"""\
service:
  name: "court"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8005
  log_level: "info"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "{tmp_path / 'test.db'}"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
platform:
  agent_id: "a-platform"
request:
  max_body_size: 1048576
disputes:
  rebuttal_deadline_seconds: 86400
  max_claim_length: 10000
  max_rebuttal_length: 10000
judges:
  panel_size: {panel_size}
  judges:{judges_yaml}
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return str(config_path)


@pytest.mark.unit
class TestJudgePanelConfig:
    """Judge panel startup validation tests."""

    def test_judge_01_even_panel_size_rejected(self, tmp_path) -> None:
        """JUDGE-01: Panel size must be odd (even size rejected at startup)."""
        config_path = _write_config(tmp_path, panel_size=2, judge_count=2)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        with pytest.raises(Exception):  # noqa: B017
            get_settings()

    def test_judge_02_panel_size_zero_rejected(self, tmp_path) -> None:
        """JUDGE-02: Panel size 0 rejected at startup."""
        config_path = _write_config(tmp_path, panel_size=0, judge_count=0)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        with pytest.raises(Exception):  # noqa: B017
            get_settings()

    def test_judge_03_negative_panel_size_rejected(self, tmp_path) -> None:
        """JUDGE-03: Panel size -1 rejected at startup."""
        config_path = _write_config(tmp_path, panel_size=-1, judge_count=0)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        with pytest.raises(Exception):  # noqa: B017
            get_settings()

    def test_judge_04_vote_count_equals_panel_size(self, tmp_path) -> None:
        """JUDGE-04: Each judge must cast exactly one vote (validated at config)."""
        config_path = _write_config(tmp_path, panel_size=1, judge_count=1)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        settings = get_settings()
        assert isinstance(settings, Settings)
        assert settings.judges.panel_size == 1
        assert len(settings.judges.judges) == 1

    def test_judge_05_panel_size_one_valid(self, tmp_path) -> None:
        """JUDGE-05: Panel size 1 is valid."""
        config_path = _write_config(tmp_path, panel_size=1, judge_count=1)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        settings = get_settings()
        assert settings.judges.panel_size == 1


@pytest.mark.unit
class TestConfigLoading:
    """Standard config loading tests."""

    def test_valid_config_loads(self, tmp_path) -> None:
        """Valid config loads without errors."""
        config_path = _write_config(tmp_path, panel_size=1, judge_count=1)
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        settings = get_settings()
        assert settings.service.name == "court"
        assert settings.server.port == 8005
        assert settings.platform.agent_id == "a-platform"
        assert settings.disputes.rebuttal_deadline_seconds == 86400

    def test_extra_fields_rejected(self, tmp_path) -> None:
        """Config with extra fields causes validation error."""
        config_path = _write_config(tmp_path, panel_size=1, judge_count=1)
        # Add an extra field to the config
        with open(config_path, "a") as f:
            f.write("unknown_section:\n  key: value\n")
        os.environ["CONFIG_PATH"] = config_path
        clear_settings_cache()
        with pytest.raises(Exception):  # noqa: B017
            get_settings()
```

**Verify:** `cd services/court && uv run ruff check tests/unit/test_config.py && uv run ruff format --check tests/unit/test_config.py`

**Commit:**
```bash
git add services/court/tests/unit/test_config.py
git commit -m "test(court): add JUDGE-01 to JUDGE-05 config validation tests"
```

---

## Task 4: Write health tests (`test_health.py`)

**Files:**
- Create: `services/court/tests/unit/routers/test_health.py`

**Test IDs:** HLTH-01 through HLTH-04 (4 tests)

```python
"""Health endpoint tests.

Covers: HLTH-01 to HLTH-04 from court-service-tests.md.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
class TestHealth:
    """Health endpoint tests."""

    async def test_hlth_01_health_schema(self, client: AsyncClient) -> None:
        """HLTH-01: Health schema is correct."""
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "started_at" in data
        assert "total_disputes" in data
        assert "active_disputes" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert isinstance(data["started_at"], str)

    async def test_hlth_02_total_disputes_accurate(self, client: AsyncClient) -> None:
        """HLTH-02: total_disputes count is accurate after filing."""
        from tests.helpers import new_task_id
        from tests.unit.routers.conftest import (
            file_dispute,
            file_dispute_payload,
        )

        # File 2 disputes
        for _ in range(2):
            await file_dispute(client, file_dispute_payload(task_id=new_task_id()))

        response = await client.get("/health")
        data = response.json()
        assert data["total_disputes"] == 2

    async def test_hlth_03_active_disputes_excludes_ruled(
        self, client: AsyncClient
    ) -> None:
        """HLTH-03: active_disputes equals count of non-ruled disputes."""
        from tests.helpers import new_task_id
        from tests.unit.routers.conftest import (
            file_dispute,
            file_dispute_payload,
            file_rebut_and_rule,
        )

        # File 3 disputes
        d1_payload = file_dispute_payload(task_id=new_task_id())
        d2_payload = file_dispute_payload(task_id=new_task_id())
        d3_payload = file_dispute_payload(task_id=new_task_id())

        await file_dispute(client, d1_payload)
        await file_dispute(client, d2_payload)
        # File and rule the third
        await file_rebut_and_rule(client, file_payload=d3_payload)

        response = await client.get("/health")
        data = response.json()
        assert data["total_disputes"] == 3
        assert data["active_disputes"] == 2

    async def test_hlth_04_uptime_monotonic(self, client: AsyncClient) -> None:
        """HLTH-04: Uptime is monotonic."""
        r1 = await client.get("/health")
        await asyncio.sleep(1.1)
        r2 = await client.get("/health")
        assert r2.json()["uptime_seconds"] > r1.json()["uptime_seconds"]
```

**Verify:** `cd services/court && uv run ruff check tests/unit/routers/test_health.py && uv run ruff format --check tests/unit/routers/test_health.py`

**Commit:**
```bash
git add services/court/tests/unit/routers/test_health.py
git commit -m "test(court): add HLTH-01 to HLTH-04 health endpoint tests"
```

---

## Task 5: Write dispute tests — File Dispute (`test_disputes.py` part 1)

**Files:**
- Create: `services/court/tests/unit/routers/test_disputes.py`

**Test IDs:** FILE-01 to FILE-17 (17 tests)

This is the largest file. Write it incrementally with one class per test category. Start with the `TestFileDispute` class.

The file will contain ALL dispute-related test classes. Start with imports and the file dispute class. Subsequent tasks add more classes to this same file.

```python
"""Dispute endpoint tests.

Covers all test categories from court-service-tests.md and court-service-auth-tests.md:
- FILE-01 to FILE-17: File Dispute
- REB-01 to REB-10: Submit Rebuttal
- RULE-01 to RULE-19: Trigger Ruling
- GET-01 to GET-05: Get Dispute
- LIST-01 to LIST-06: List Disputes
- HTTP-01: HTTP Method Misuse
- SEC-01 to SEC-03: Cross-Cutting Security
- LIFE-01 to LIFE-05: Dispute Lifecycle
- AUTH-01 to AUTH-16: Platform JWS Validation
- PUB-01 to PUB-03: Public Endpoints
- IDEP-01 to IDEP-03: Identity Dependency
- REPLAY-01 to REPLAY-02: Token Replay
- PREC-01 to PREC-06: Error Precedence
- SEC-AUTH-01 to SEC-AUTH-03: Auth Security
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from court_service.core.state import get_app_state
from tests.helpers import (
    make_jws_token,
    make_mock_identity_client,
    make_mock_task_board_client,
    make_tampered_jws,
    make_task_data,
    new_agent_id,
    new_escrow_id,
    new_task_id,
)
from tests.unit.routers.conftest import (
    CLAIMANT_ID,
    PLATFORM_AGENT_ID,
    RESPONDENT_ID,
    ROGUE_AGENT_ID,
    file_and_rebut,
    file_dispute,
    file_dispute_payload,
    file_rebut_and_rule,
    inject_central_bank_error,
    inject_identity_error,
    inject_identity_verify,
    inject_judge,
    inject_reputation_error,
    inject_task_board_error,
    inject_task_board_response,
    rebuttal_payload,
    ruling_payload,
    token_body,
)

if TYPE_CHECKING:
    from httpx import AsyncClient

UUID4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
DISPUTE_ID_PATTERN = re.compile(r"^disp-" + UUID4_PATTERN.pattern[1:])
VOTE_ID_PATTERN = re.compile(r"^vote-" + UUID4_PATTERN.pattern[1:])
ISO8601_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@pytest.mark.unit
class TestFileDispute:
    """FILE-01 to FILE-17: File dispute tests."""

    async def test_file_01_valid_dispute(self, client: AsyncClient) -> None:
        """FILE-01: File a valid dispute returns 201 with correct status."""
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 201
        data = response.json()
        assert DISPUTE_ID_PATTERN.match(data["dispute_id"])
        assert data["status"] == "rebuttal_pending"

    async def test_file_02_response_includes_all_fields(self, client: AsyncClient) -> None:
        """FILE-02: Response includes all dispute fields."""
        data = await file_dispute(client)
        expected_fields = {
            "dispute_id", "task_id", "claimant_id", "respondent_id",
            "claim", "rebuttal", "status", "rebuttal_deadline",
            "worker_pct", "ruling_summary", "escrow_id",
            "filed_at", "rebutted_at", "ruled_at", "votes",
        }
        assert expected_fields.issubset(set(data.keys()))
        assert data["rebuttal"] is None
        assert data["worker_pct"] is None
        assert data["ruling_summary"] is None
        assert data["rebutted_at"] is None
        assert data["ruled_at"] is None
        assert data["votes"] == []
        assert ISO8601_PATTERN.match(data["filed_at"])
        assert ISO8601_PATTERN.match(data["rebuttal_deadline"])

    async def test_file_03_rebuttal_deadline_calculated(self, client: AsyncClient) -> None:
        """FILE-03: Rebuttal deadline is filed_at + configured seconds."""
        from datetime import datetime, timedelta, timezone

        data = await file_dispute(client)
        filed_at = datetime.fromisoformat(data["filed_at"])
        deadline = datetime.fromisoformat(data["rebuttal_deadline"])
        # Default is 86400 seconds (24 hours)
        expected = filed_at + timedelta(seconds=86400)
        # Allow 5 second tolerance
        assert abs((deadline - expected).total_seconds()) < 5

    async def test_file_04_duplicate_task_rejected(self, client: AsyncClient) -> None:
        """FILE-04: Duplicate dispute for same task is rejected."""
        task_id = new_task_id()
        payload = file_dispute_payload(task_id=task_id)
        await file_dispute(client, payload)
        # Attempt duplicate
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 409
        assert response.json()["error"] == "DISPUTE_ALREADY_EXISTS"

    async def test_file_05_task_not_found(self, client: AsyncClient) -> None:
        """FILE-05: Task not found in Task Board."""
        inject_task_board_error(ServiceError("TASK_NOT_FOUND", "Not found", status_code=404))
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 404
        assert response.json()["error"] == "TASK_NOT_FOUND"

    async def test_file_06_missing_claim(self, client: AsyncClient) -> None:
        """FILE-06: Missing claim text."""
        payload = file_dispute_payload()
        del payload["claim"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_07_empty_claim(self, client: AsyncClient) -> None:
        """FILE-07: Empty claim text."""
        payload = file_dispute_payload(claim="")
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_08_claim_too_long(self, client: AsyncClient) -> None:
        """FILE-08: Claim exceeds 10,000 characters."""
        payload = file_dispute_payload(claim="x" * 10001)
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_09_missing_task_id(self, client: AsyncClient) -> None:
        """FILE-09: Missing task_id."""
        payload = file_dispute_payload()
        del payload["task_id"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_10_missing_claimant_id(self, client: AsyncClient) -> None:
        """FILE-10: Missing claimant_id."""
        payload = file_dispute_payload()
        del payload["claimant_id"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_11_missing_respondent_id(self, client: AsyncClient) -> None:
        """FILE-11: Missing respondent_id."""
        payload = file_dispute_payload()
        del payload["respondent_id"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_12_missing_escrow_id(self, client: AsyncClient) -> None:
        """FILE-12: Missing escrow_id."""
        payload = file_dispute_payload()
        del payload["escrow_id"]
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_13_wrong_action(self, client: AsyncClient) -> None:
        """FILE-13: Wrong action value."""
        payload = file_dispute_payload(action="submit_rebuttal")
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_file_14_non_platform_signer(self, client: AsyncClient) -> None:
        """FILE-14: Non-platform signer is rejected."""
        payload = file_dispute_payload()
        inject_identity_verify(ROGUE_AGENT_ID, payload)
        response = await client.post(
            "/disputes/file", json=token_body(payload, kid=ROGUE_AGENT_ID)
        )
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_file_15_tampered_jws(self, client: AsyncClient) -> None:
        """FILE-15: Tampered JWS is rejected."""
        payload = file_dispute_payload()
        state = get_app_state()
        state.identity_client = make_mock_identity_client(
            verify_response={"valid": False, "agent_id": None, "payload": None}
        )
        token = make_tampered_jws(payload, kid=PLATFORM_AGENT_ID)
        response = await client.post("/disputes/file", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_file_16_missing_token(self, client: AsyncClient) -> None:
        """FILE-16: Missing token field."""
        response = await client.post("/disputes/file", json={})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_file_17_task_board_unavailable(self, client: AsyncClient) -> None:
        """FILE-17: Task Board unavailable."""
        inject_task_board_error(ConnectionError("Connection refused"))
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post("/disputes/file", json=token_body(payload))
        assert response.status_code == 502
        assert response.json()["error"] == "TASK_BOARD_UNAVAILABLE"
```

**Verify:** `cd services/court && uv run ruff check tests/unit/routers/test_disputes.py && uv run ruff format --check tests/unit/routers/test_disputes.py`

**Commit:**
```bash
git add services/court/tests/unit/routers/test_disputes.py
git commit -m "test(court): add FILE-01 to FILE-17 file dispute tests"
```

---

## Task 6: Add Rebuttal tests to `test_disputes.py`

**Test IDs:** REB-01 to REB-10 (10 tests)

Append the `TestSubmitRebuttal` class to `test_disputes.py`.

The class contains tests for submitting rebuttals — valid rebuttal, not found, duplicate, wrong status, missing/empty/too-long text, wrong action, non-platform signer, and status unchanged after rebuttal.

Each test follows the spec precisely: setup (file a dispute first), action (POST rebuttal), assert status code and error code.

**Commit:**
```bash
git add services/court/tests/unit/routers/test_disputes.py
git commit -m "test(court): add REB-01 to REB-10 rebuttal tests"
```

---

## Task 7: Add Ruling tests to `test_disputes.py`

**Test IDs:** RULE-01 to RULE-19 (19 tests)

Append the `TestTriggerRuling` class. This is the most complex category — it tests:
- Valid ruling with various worker_pct values (0, 50, 70, 73, 100)
- Status changes, timestamps, vote structure
- Side-effect verification (Central Bank called, Reputation called)
- Error conditions (not found, already ruled, judge/bank/reputation unavailable)
- Ruling without rebuttal

Key pattern: Most tests call `file_and_rebut()` first, then inject a specific judge mock, then trigger ruling.

**Commit:**
```bash
git add services/court/tests/unit/routers/test_disputes.py
git commit -m "test(court): add RULE-01 to RULE-19 ruling tests"
```

---

## Task 8: Add GET, LIST, HTTP, SEC, LIFE tests to `test_disputes.py`

**Test IDs:** GET-01 to GET-05, LIST-01 to LIST-06, HTTP-01, SEC-01 to SEC-03, LIFE-01 to LIFE-05 (22 tests)

Append these classes:
- `TestGetDispute` — 5 tests (GET filed, GET ruled, vote structure, not found, no auth required)
- `TestListDisputes` — 6 tests (empty, all, filter by task_id, filter by status, both filters, no auth)
- `TestHTTPMethods` — 1 test (parametrized over 14 method/path combos, each asserts 405)
- `TestCrossCuttingSecurity` — 3 tests (error envelope, no leakage, ID format)
- `TestDisputeLifecycle` — 5 tests (full lifecycle, file+rule no rebuttal, no duplicate, no rebuttal after ruling, no double ruling)

**Commit:**
```bash
git add services/court/tests/unit/routers/test_disputes.py
git commit -m "test(court): add GET, LIST, HTTP, SEC, LIFE test categories"
```

---

## Task 9: Add Auth tests to `test_disputes.py`

**Test IDs:** AUTH-01 to AUTH-16, PUB-01 to PUB-03, IDEP-01 to IDEP-03, REPLAY-01 to REPLAY-02, PREC-01 to PREC-06, SEC-AUTH-01 to SEC-AUTH-03 (33 tests)

Append these classes:
- `TestPlatformJWS` — AUTH-01 to AUTH-16 (valid JWS on each endpoint, missing/null/non-string/empty/malformed token, tampered, non-platform signer, wrong action, missing action, malformed JSON, non-object JSON)
- `TestPublicEndpoints` — PUB-01 to PUB-03 (GET endpoints need no auth)
- `TestIdentityDependency` — IDEP-01 to IDEP-03 (identity down, timeout, unexpected response)
- `TestTokenReplay` — REPLAY-01 to REPLAY-02 (rebuttal token on file, file token on rule)
- `TestErrorPrecedence` — PREC-01 to PREC-06 (content-type before token, body size before token, JSON before token, token before payload, action before signer, identity unavailable before payload)
- `TestAuthSecurity` — SEC-AUTH-01 to SEC-AUTH-03 (error envelope, no leakage, cross-service replay)

**Commit:**
```bash
git add services/court/tests/unit/routers/test_disputes.py
git commit -m "test(court): add AUTH, PUB, IDEP, REPLAY, PREC, SEC-AUTH test categories"
```

---

## Task 10: Create stub conftest files and run CI

**Files:**
- Verify: `services/court/tests/integration/conftest.py` (already exists as `test_endpoints.py`)
- Verify: `services/court/tests/performance/` (already has `__init__.py`)

**Step 1: Verify all files pass ruff**

Run: `cd services/court && uv run ruff check tests/ && uv run ruff format --check tests/`
Expected: All pass

**Step 2: Run codespell**

Run: `cd services/court && uv run codespell tests/`
Expected: No spelling errors

**Step 3: Run full CI**

Run: `cd services/court && just ci-quiet`

Expected results:
- `ruff check` — PASS
- `ruff format --check` — PASS
- `codespell` — PASS
- `mypy` — PASS (only checks `src/`, tests excluded)
- `pyright` — PASS (only checks `src/`)
- `bandit` — PASS (tests excluded)
- `pytest` — FAIL (expected — `ImportError` from `court_service.config`, etc.)

The CI will report test failures. This is correct — the tests cannot run because the service is not implemented. The important thing is that all static analysis passes.

**Step 4: Final commit**

```bash
git add -A services/court/tests/
git commit -m "test(court): complete 108 acceptance tests (75 core + 33 auth)"
```

---

## Summary

| Task | Files | Tests | Description |
|------|-------|-------|-------------|
| 1 | `tests/helpers.py` | — | Shared JWS, mock, and ID helpers |
| 2 | `tests/conftest.py`, `tests/unit/conftest.py`, `tests/unit/routers/conftest.py` | — | Fixture infrastructure |
| 3 | `tests/unit/test_config.py` | 7 | JUDGE-01 to JUDGE-05 + config loading |
| 4 | `tests/unit/routers/test_health.py` | 4 | HLTH-01 to HLTH-04 |
| 5 | `tests/unit/routers/test_disputes.py` | 17 | FILE-01 to FILE-17 |
| 6 | (append to test_disputes.py) | 10 | REB-01 to REB-10 |
| 7 | (append to test_disputes.py) | 19 | RULE-01 to RULE-19 |
| 8 | (append to test_disputes.py) | 22 | GET, LIST, HTTP, SEC, LIFE |
| 9 | (append to test_disputes.py) | 33 | AUTH, PUB, IDEP, REPLAY, PREC, SEC-AUTH |
| 10 | — | — | CI validation |
| **Total** | | **112** | 108 spec + 4 bonus config tests |
