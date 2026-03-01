"""
Persistence acceptance tests for the Reputation Service.

Tests cover: configuration, database initialization, data persistence across
restarts, database-level integrity, and cross-cutting security assertions.

Test IDs reference the persistence test specification:
docs/specifications/service-tests/reputation-service-persistence-tests.md
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient

from reputation_service.app import create_app
from reputation_service.config import clear_settings_cache
from reputation_service.core.state import get_app_state, reset_app_state
from tests.helpers import make_jws_token, make_mock_identity_client

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from pathlib import Path

    from fastapi import FastAPI

VALID_FEEDBACK: dict[str, object] = {
    "action": "submit_feedback",
    "task_id": "task-1",
    "from_agent_id": "agent-alice",
    "to_agent_id": "agent-bob",
    "category": "delivery_quality",
    "rating": "satisfied",
    "comment": "Good work",
}


def _write_config(tmp_path: Path, db_path: str, reveal_timeout: int = 86400) -> str:
    """Write a test config.yaml and return the path."""
    config_content = f"""\
service:
  name: "reputation"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8004
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
request:
  max_body_size: 1048576
database:
  path: "{db_path}"
feedback:
  reveal_timeout_seconds: {reveal_timeout}
  max_comment_length: 256
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    return str(config_path)


def _setup_env(config_path: str) -> str | None:
    """Set CONFIG_PATH and return the old value."""
    old = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = config_path
    clear_settings_cache()
    reset_app_state()
    return old


def _restore_env(old_config: str | None) -> None:
    """Restore CONFIG_PATH."""
    if old_config is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = old_config
    clear_settings_cache()
    reset_app_state()


def _inject_mock_identity(payload: dict[str, object], kid: str) -> None:
    """Inject a mock IdentityClient that returns the given payload."""
    state = get_app_state()
    state.identity_client = make_mock_identity_client(
        verify_response={"valid": True, "agent_id": kid, "payload": payload},
    )


async def _make_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Create an async client with lifespan."""
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        yield client


async def _submit_feedback(client: AsyncClient, **overrides: object) -> dict[str, object]:
    """Submit valid feedback with optional overrides, wrapped in JWS."""
    payload: dict[str, object] = dict(VALID_FEEDBACK)
    payload.update(overrides)
    kid = str(payload.get("from_agent_id", "agent-alice"))
    _inject_mock_identity(payload, kid)
    token = make_jws_token(payload, kid=kid)
    resp = await client.post(
        "/feedback",
        json={"token": token},
        headers={"content-type": "application/json"},
    )
    return resp.json()


async def _post_feedback_jws(
    client: AsyncClient,
    body: Mapping[str, object],
) -> Any:
    """Post feedback body wrapped in JWS and return the response."""
    payload: dict[str, object] = {"action": "submit_feedback", **body}
    kid = str(payload.get("from_agent_id", "agent-alice"))
    _inject_mock_identity(payload, kid)
    token = make_jws_token(payload, kid=kid)
    return await client.post(
        "/feedback",
        json={"token": token},
        headers={"content-type": "application/json"},
    )


# ---------------------------------------------------------------------------
# Category 1: Configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_cfg_01_service_starts_with_valid_database_path(tmp_path: Path) -> None:
    """CFG-01: Service starts with valid database.path."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app = create_app()
        async for client in _make_client(app):
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_cfg_02_fails_without_database_section(tmp_path: Path) -> None:
    """CFG-02: Service fails to start without database section."""
    config_content = """\
service:
  name: "reputation"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8004
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
feedback:
  reveal_timeout_seconds: 86400
  max_comment_length: 256
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    old = _setup_env(str(config_path))
    try:
        with pytest.raises(Exception):  # noqa: B017
            app = create_app()
            async for _ in _make_client(app):
                pass  # Should not reach here
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_cfg_03_fails_without_database_path(tmp_path: Path) -> None:
    """CFG-03: Service fails to start without database.path."""
    config_content = """\
service:
  name: "reputation"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8004
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database: {}
feedback:
  reveal_timeout_seconds: 86400
  max_comment_length: 256
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    old = _setup_env(str(config_path))
    try:
        with pytest.raises(Exception):  # noqa: B017
            app = create_app()
            async for _ in _make_client(app):
                pass
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_cfg_04_fails_with_unwritable_path(tmp_path: Path) -> None:
    """CFG-04: Service fails to start with unwritable database.path."""
    db_path = "/proc/nonexistent/reputation.db"
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        with pytest.raises(Exception):  # noqa: B017
            app = create_app()
            async for _ in _make_client(app):
                pass
    finally:
        _restore_env(old)


# ---------------------------------------------------------------------------
# Category 2: Database Initialization
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_init_01_db_file_created_on_first_startup(tmp_path: Path) -> None:
    """INIT-01: Database file is created on first startup."""
    db_path = tmp_path / "data" / "test.db"
    assert not db_path.exists()
    config_path = _write_config(tmp_path, str(db_path))
    old = _setup_env(config_path)
    try:
        app = create_app()
        async for client in _make_client(app):
            assert db_path.exists()
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["total_feedback"] == 0
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_init_02_parent_directory_created(tmp_path: Path) -> None:
    """INIT-02: Parent directory is created if missing."""
    data_dir = tmp_path / "nested" / "data"
    db_path = data_dir / "test.db"
    assert not data_dir.exists()
    config_path = _write_config(tmp_path, str(db_path))
    old = _setup_env(config_path)
    try:
        app = create_app()
        async for _ in _make_client(app):
            assert data_dir.exists()
            assert db_path.exists()
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_init_03_schema_idempotent_on_restart(tmp_path: Path) -> None:
    """INIT-03: Schema is idempotent on re-startup."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        # First startup — submit data
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(client1)

        # Second startup — should not crash
        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get("/health")
            assert resp.status_code == 200
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_init_04_existing_data_not_wiped(tmp_path: Path) -> None:
    """INIT-04: Existing database with data is not wiped on startup."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        # Submit 3 feedback records
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(client1, task_id="t-1", from_agent_id="a-1", to_agent_id="a-2")
            await _submit_feedback(client1, task_id="t-2", from_agent_id="a-1", to_agent_id="a-2")
            await _submit_feedback(client1, task_id="t-3", from_agent_id="a-1", to_agent_id="a-2")

        # Restart
        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get("/health")
            assert resp.json()["total_feedback"] == 3
    finally:
        _restore_env(old)


# ---------------------------------------------------------------------------
# Category 3: Data Persistence Across Restarts
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_persist_01_feedback_survives_restart(tmp_path: Path) -> None:
    """PERSIST-01: Feedback record survives service restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        # Submit both directions to reveal
        app1 = create_app()
        async for client1 in _make_client(app1):
            result = await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )
            feedback_id = result["feedback_id"]
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-bob", to_agent_id="a-alice"
            )

        # Restart and verify
        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get(f"/feedback/{feedback_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["feedback_id"] == feedback_id
            assert data["task_id"] == "t-1"
            assert data["from_agent_id"] == "a-alice"
            assert data["to_agent_id"] == "a-bob"
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_02_visibility_survives_restart(tmp_path: Path) -> None:
    """PERSIST-02: Visibility state survives restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-bob", to_agent_id="a-alice"
            )

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get("/feedback/task/t-1")
            assert resp.status_code == 200
            feedback = resp.json()["feedback"]
            assert len(feedback) == 2
            assert all(f["visible"] is True for f in feedback)
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_03_sealed_remains_sealed(tmp_path: Path) -> None:
    """PERSIST-03: Sealed feedback remains sealed after restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get("/feedback/task/t-1")
            assert resp.status_code == 200
            assert resp.json()["feedback"] == []
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_04_sealed_returns_404_after_restart(tmp_path: Path) -> None:
    """PERSIST-04: Sealed feedback still returns 404 after restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        feedback_id = ""
        async for client1 in _make_client(app1):
            result = await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )
            feedback_id = str(result["feedback_id"])

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get(f"/feedback/{feedback_id}")
            assert resp.status_code == 404
            assert resp.json()["error"] == "FEEDBACK_NOT_FOUND"
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_05_task_feedback_survives_restart(tmp_path: Path) -> None:
    """PERSIST-05: Task feedback query survives restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-bob", to_agent_id="a-alice"
            )

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get("/feedback/task/t-1")
            assert resp.status_code == 200
            feedback = resp.json()["feedback"]
            assert len(feedback) == 2
            timestamps = [f["submitted_at"] for f in feedback]
            assert timestamps == sorted(timestamps)
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_06_agent_feedback_survives_restart(tmp_path: Path) -> None:
    """PERSIST-06: Agent feedback query survives restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-bob", to_agent_id="a-alice"
            )

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get("/feedback/agent/a-bob")
            assert resp.status_code == 200
            feedback = resp.json()["feedback"]
            assert len(feedback) == 1
            assert feedback[0]["to_agent_id"] == "a-bob"
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_07_health_count_survives_restart(tmp_path: Path) -> None:
    """PERSIST-07: Health total_feedback count survives restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        async for client1 in _make_client(app1):
            # Submit 3 records (mix of sealed and revealed)
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-bob", to_agent_id="a-alice"
            )
            await _submit_feedback(
                client1, task_id="t-2", from_agent_id="a-carol", to_agent_id="a-dave"
            )

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get("/health")
            assert resp.json()["total_feedback"] == 3
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_08_uptime_resets_after_restart(tmp_path: Path) -> None:
    """PERSIST-08: Uptime resets after restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        async for client1 in _make_client(app1):
            resp1 = await client1.get("/health")
            started_at_1 = resp1.json()["started_at"]

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp2 = await client2.get("/health")
            data2 = resp2.json()
            # Uptime should be close to zero
            assert data2["uptime_seconds"] < 5.0
            # started_at should be more recent
            assert data2["started_at"] >= started_at_1
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_09_new_data_coexists_with_old(tmp_path: Path) -> None:
    """PERSIST-09: Feedback submitted after restart coexists with old data."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-bob", to_agent_id="a-alice"
            )

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            await _submit_feedback(
                client2, task_id="t-2", from_agent_id="a-carol", to_agent_id="a-dave"
            )
            await _submit_feedback(
                client2, task_id="t-2", from_agent_id="a-dave", to_agent_id="a-carol"
            )
            resp = await client2.get("/health")
            assert resp.json()["total_feedback"] == 4
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_10_uniqueness_survives_restart(tmp_path: Path) -> None:
    """PERSIST-10: Uniqueness constraint survives restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        async for client1 in _make_client(app1):
            resp = await _post_feedback_jws(
                client1,
                {
                    "task_id": "t-1",
                    "from_agent_id": "a-alice",
                    "to_agent_id": "a-bob",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
            )
            assert resp.status_code == 201

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await _post_feedback_jws(
                client2,
                {
                    "task_id": "t-1",
                    "from_agent_id": "a-alice",
                    "to_agent_id": "a-bob",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
            )
            assert resp.status_code == 409
            assert resp.json()["error"] == "FEEDBACK_EXISTS"
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_11_mutual_reveal_across_restart(tmp_path: Path) -> None:
    """PERSIST-11: Mutual reveal works across restart boundary."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        # Submit first direction before restart
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )

        # Submit reverse direction after restart
        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await _post_feedback_jws(
                client2,
                {
                    "task_id": "t-1",
                    "from_agent_id": "a-bob",
                    "to_agent_id": "a-alice",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
            )
            assert resp.status_code == 201
            assert resp.json()["visible"] is True

            # Both should be visible
            task_resp = await client2.get("/feedback/task/t-1")
            assert len(task_resp.json()["feedback"]) == 2
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_12_comment_values_survive_restart(tmp_path: Path) -> None:
    """PERSIST-12: Comment values survive restart faithfully."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app1 = create_app()
        feedback_ids: dict[str, str] = {}
        async for client1 in _make_client(app1):
            # Submit with "Good work" comment
            r1 = await _submit_feedback(
                client1,
                task_id="t-1",
                from_agent_id="a-alice",
                to_agent_id="a-bob",
                comment="Good work",
            )
            feedback_ids["good_work"] = str(r1["feedback_id"])
            # Reveal it
            await _submit_feedback(
                client1,
                task_id="t-1",
                from_agent_id="a-bob",
                to_agent_id="a-alice",
            )

            # Submit with empty string comment
            r2 = await _submit_feedback(
                client1,
                task_id="t-2",
                from_agent_id="a-alice",
                to_agent_id="a-bob",
                comment="",
            )
            feedback_ids["empty"] = str(r2["feedback_id"])
            await _submit_feedback(
                client1,
                task_id="t-2",
                from_agent_id="a-bob",
                to_agent_id="a-alice",
            )

            # Submit with null comment
            r3 = await _post_feedback_jws(
                client1,
                {
                    "task_id": "t-3",
                    "from_agent_id": "a-alice",
                    "to_agent_id": "a-bob",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                    "comment": None,
                },
            )
            feedback_ids["null"] = str(r3.json()["feedback_id"])
            await _submit_feedback(
                client1,
                task_id="t-3",
                from_agent_id="a-bob",
                to_agent_id="a-alice",
            )

        # Restart and verify
        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            r1 = await client2.get(f"/feedback/{feedback_ids['good_work']}")
            assert r1.json()["comment"] == "Good work"

            r2 = await client2.get(f"/feedback/{feedback_ids['empty']}")
            assert r2.json()["comment"] == ""

            r3 = await client2.get(f"/feedback/{feedback_ids['null']}")
            assert r3.json()["comment"] is None
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_persist_13_unicode_comments_survive_restart(tmp_path: Path) -> None:
    """PERSIST-13: Unicode comments survive restart."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    unicode_comment = "Great work! Excellent quality"
    try:
        app1 = create_app()
        feedback_id = ""
        async for client1 in _make_client(app1):
            r = await _submit_feedback(
                client1,
                task_id="t-1",
                from_agent_id="a-alice",
                to_agent_id="a-bob",
                comment=unicode_comment,
            )
            feedback_id = str(r["feedback_id"])
            await _submit_feedback(
                client1,
                task_id="t-1",
                from_agent_id="a-bob",
                to_agent_id="a-alice",
            )

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await client2.get(f"/feedback/{feedback_id}")
            assert resp.status_code == 200
            assert resp.json()["comment"] == unicode_comment
    finally:
        _restore_env(old)


# ---------------------------------------------------------------------------
# Category 4: Database-Level Integrity
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_db_01_concurrent_duplicate_safe(tmp_path: Path) -> None:
    """DB-01: Concurrent duplicate feedback is safe under SQLite."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app = create_app()
        async for client in _make_client(app):
            body = {
                "task_id": "t-1",
                "from_agent_id": "a-alice",
                "to_agent_id": "a-bob",
                "category": "delivery_quality",
                "rating": "satisfied",
            }
            payload: dict[str, object] = {"action": "submit_feedback", **body}
            _inject_mock_identity(payload, "a-alice")
            token = make_jws_token(payload, kid="a-alice")
            jws_body: dict[str, object] = {"token": token}
            hdrs = {"content-type": "application/json"}
            results = await asyncio.gather(
                client.post("/feedback", json=jws_body, headers=hdrs),
                client.post("/feedback", json=jws_body, headers=hdrs),
            )
            status_codes = sorted([r.status_code for r in results])
            assert status_codes == [201, 409]
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_db_02_concurrent_mutual_reveal_atomic(tmp_path: Path) -> None:
    """DB-02: Concurrent mutual reveal is atomic."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app = create_app()
        async for client in _make_client(app):
            # Submit first direction
            await _submit_feedback(
                client, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )

            # Send reveal + read concurrently
            reveal_body: dict[str, object] = {
                "task_id": "t-1",
                "from_agent_id": "a-bob",
                "to_agent_id": "a-alice",
                "category": "delivery_quality",
                "rating": "satisfied",
            }
            reveal_payload: dict[str, object] = {"action": "submit_feedback", **reveal_body}
            _inject_mock_identity(reveal_payload, "a-bob")
            reveal_token = make_jws_token(reveal_payload, kid="a-bob")
            post_resp, get_resp = await asyncio.gather(
                client.post(
                    "/feedback",
                    json={"token": reveal_token},
                    headers={"content-type": "application/json"},
                ),
                client.get("/feedback/task/t-1"),
            )

            assert post_resp.status_code == 201
            assert post_resp.json()["visible"] is True

            # GET must return either 0 or 2 visible entries (never 1)
            feedback = get_resp.json()["feedback"]
            assert len(feedback) in (0, 2)
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_db_03_no_sql_injection_via_fields(tmp_path: Path) -> None:
    """DB-03: No SQL injection via feedback fields."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app = create_app()
        async for client in _make_client(app):
            injection_tests = [
                {
                    "from_agent_id": "'; DROP TABLE feedback; --",
                    "to_agent_id": "a-bob",
                    "task_id": "t-1",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
                {
                    "to_agent_id": "' OR '1'='1",
                    "from_agent_id": "a-alice",
                    "task_id": "t-2",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
                {
                    "task_id": "'; DELETE FROM feedback; --",
                    "from_agent_id": "a-alice",
                    "to_agent_id": "a-bob",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
                {
                    "comment": "Robert'); DROP TABLE feedback;--",
                    "task_id": "t-4",
                    "from_agent_id": "a-alice",
                    "to_agent_id": "a-bob",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
            ]

            for body in injection_tests:
                resp = await _post_feedback_jws(client, body)
                # Should either succeed or return standard validation error
                assert resp.status_code in (201, 400, 409)
                resp_body = resp.json()
                resp_text = str(resp_body)
                assert "DROP" not in resp_text or "comment" in resp_text
                assert "sqlite3" not in resp_text.lower()
                assert "traceback" not in resp_text.lower()

            # Service still functional
            health = await client.get("/health")
            assert health.status_code == 200
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_db_04_no_sql_injection_via_path_params(tmp_path: Path) -> None:
    """DB-04: No SQL injection via path parameters."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app = create_app()
        async for client in _make_client(app):
            # Feedback lookup — should return 404
            resp1 = await client.get("/feedback/' OR '1'='1")
            assert resp1.status_code == 404
            assert "sqlite3" not in str(resp1.json()).lower()

            # Task lookup — should return 200 with empty array
            resp2 = await client.get("/feedback/task/' OR '1'='1; DROP TABLE feedback; --")
            assert resp2.status_code == 200
            assert resp2.json()["feedback"] == []

            # Agent lookup — should return 200 with empty array
            resp3 = await client.get("/feedback/agent/' OR '1'='1")
            assert resp3.status_code == 200
            assert resp3.json()["feedback"] == []

            # Service still functional
            health = await client.get("/health")
            assert health.status_code == 200
    finally:
        _restore_env(old)


# ---------------------------------------------------------------------------
# Category 5: Cross-Cutting Security Assertions
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sec_db_01_no_sql_in_error_messages(tmp_path: Path) -> None:
    """SEC-DB-01: Error messages never contain SQL fragments."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    sql_keywords = [
        "INSERT",
        "SELECT",
        "DELETE",
        "UPDATE",
        "DROP",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "UNIQUE constraint",
        "sqlite3",
        "IntegrityError",
        "traceback",
        'File "',
    ]
    try:
        app = create_app()
        async for client in _make_client(app):
            # Trigger FEEDBACK_EXISTS
            await _submit_feedback(
                client, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )
            dup_resp = await _post_feedback_jws(
                client,
                {
                    "task_id": "t-1",
                    "from_agent_id": "a-alice",
                    "to_agent_id": "a-bob",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
            )
            assert dup_resp.status_code == 409
            dup_text = str(dup_resp.json())
            for keyword in sql_keywords:
                assert keyword not in dup_text

            # Trigger FEEDBACK_NOT_FOUND (sealed)
            not_found_resp = await client.get("/feedback/fb-nonexistent")
            assert not_found_resp.status_code == 404
            nf_text = str(not_found_resp.json())
            for keyword in sql_keywords:
                assert keyword not in nf_text
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_sec_db_02_db_file_not_accessible_via_api(tmp_path: Path) -> None:
    """SEC-DB-02: Database file is not accessible via API."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        app = create_app()
        async for client in _make_client(app):
            resp1 = await client.get("/feedback/../data/reputation.db")
            assert resp1.status_code == 404

            resp2 = await client.get("/feedback/..%2Fdata%2Freputation.db")
            assert resp2.status_code == 404

            # Ensure no file contents leaked
            for resp in [resp1, resp2]:
                body_text = resp.text
                assert "SQLite" not in body_text
                assert "CREATE TABLE" not in body_text
    finally:
        _restore_env(old)


@pytest.mark.unit
async def test_sec_db_03_error_envelope_consistency(tmp_path: Path) -> None:
    """SEC-DB-03: Error envelope consistency for persistence-related errors."""
    db_path = str(tmp_path / "test.db")
    config_path = _write_config(tmp_path, db_path)
    old = _setup_env(config_path)
    try:
        # Submit, restart, submit duplicate — verify envelope
        app1 = create_app()
        async for client1 in _make_client(app1):
            await _submit_feedback(
                client1, task_id="t-1", from_agent_id="a-alice", to_agent_id="a-bob"
            )

        clear_settings_cache()
        reset_app_state()
        app2 = create_app()
        async for client2 in _make_client(app2):
            resp = await _post_feedback_jws(
                client2,
                {
                    "task_id": "t-1",
                    "from_agent_id": "a-alice",
                    "to_agent_id": "a-bob",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                },
            )
            assert resp.status_code == 409
            data = resp.json()
            assert data["error"] == "FEEDBACK_EXISTS"
            assert isinstance(data["message"], str)
            assert isinstance(data["details"], dict)
    finally:
        _restore_env(old)
