"""Unit tests for demo_replay.engine — scenario step dispatch with mocked clients.

Covers item 6(b) (an unknown scenario action becomes a hard error instead of
being silently skipped) and item 7 (dispatch is exercised with mocked
clients rather than a live stack, and every client call uses the
config-resolved URL, never a hardcoded one).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from demo_replay import clients
from demo_replay.config import PlatformConfig
from demo_replay.engine import ReplayEngine, UnknownActionError
from test_mp07_task_operations import REASON, _engine as _durable_engine, _response, _task


def _make_config() -> PlatformConfig:
    return PlatformConfig(
        identity_url="http://identity.test",
        bank_url="http://bank.test",
        task_board_url="http://taskboard.test",
        reputation_url="http://reputation.test",
        court_url="http://court.test",
        platform_key_path=Path("keys/platform.key"),
        token_ttl_seconds=300,
    )


def _make_engine(steps: list[dict[str, object]] | None = None) -> ReplayEngine:
    scenario = {
        "name": "Test Scenario",
        "agents": [{"handle": "alice", "name": "Alice"}, {"handle": "bob", "name": "Bob"}],
        "steps": steps or [],
    }
    engine = ReplayEngine(scenario, _make_config())
    engine.agents["alice"] = MagicMock(agent_id="a-alice")
    engine.agents["bob"] = MagicMock(agent_id="a-bob")
    return engine


@pytest.mark.unit
class TestUnknownActionIsAHardError:
    @pytest.mark.asyncio
    async def test_unknown_action_raises(self) -> None:
        engine = _make_engine()
        http = MagicMock()

        with pytest.raises(UnknownActionError, match="mystery_action"):
            await engine._execute_step(http, {"action": "mystery_action"})

    @pytest.mark.asyncio
    async def test_reveal_feedback_is_now_unknown(self) -> None:
        """reveal_feedback was removed (no such real API endpoint, GAP-A13)."""
        engine = _make_engine()
        http = MagicMock()

        with pytest.raises(UnknownActionError):
            await engine._execute_step(http, {"action": "reveal_feedback"})


@pytest.mark.unit
class TestFeedbackDispatchDropsRole:
    @pytest.mark.asyncio
    async def test_do_feedback_calls_submit_feedback_without_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        step = {
            "action": "feedback",
            "agent": "alice",
            "task_ref": "task",
            "to_agent_id": "bob",
            "category": "spec_quality",
            "rating": "satisfied",
            "comment": "nice work",
        }
        engine = _durable_engine([step])
        engine._config = _make_config()
        mock_submit = AsyncMock(
            return_value={
                "feedback_id": "f-1",
                "task_id": "t-00000000-0000-4000-8000-000000000001",
                "from_agent_id": "a-alice",
                "to_agent_id": "a-bob",
                "category": "spec_quality",
                "rating": "satisfied",
                "comment": "nice work",
                "submitted_at": "2026-10-22T00:00:00Z",
                "visible": False,
            }
        )
        monkeypatch.setattr(clients, "submit_feedback", mock_submit)
        await engine._execute_step(MagicMock(), step)

        mock_submit.assert_awaited_once()
        call_kwargs = mock_submit.call_args.kwargs
        assert "role" not in call_kwargs
        assert call_kwargs["reputation_url"] == "http://reputation.test"
        assert call_kwargs["to_agent_id"] == "a-bob"


@pytest.mark.unit
class TestDispatchUsesConfigResolvedUrls:
    @pytest.mark.asyncio
    async def test_do_register_uses_config_identity_and_bank_urls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _make_engine()
        mock_register = AsyncMock(return_value={"agent_id": "a-alice"})
        mock_create_account = AsyncMock(return_value={"status": "created"})
        monkeypatch.setattr(clients, "register_agent", mock_register)
        monkeypatch.setattr(clients, "create_account", mock_create_account)
        engine.platform = MagicMock(agent_id="a-platform")
        engine._store = MagicMock(actors={"alice": MagicMock(registration=None, agent_id=None)})

        await engine._do_register(MagicMock(), {"agent": "alice"})

        mock_register.assert_awaited_once()
        assert mock_register.call_args[0][2] == "http://identity.test"
        mock_create_account.assert_awaited_once()
        assert mock_create_account.call_args[0][3] == "http://bank.test"

    @pytest.mark.asyncio
    async def test_retired_one_shot_posting_is_never_used_without_a_durable_store(
        self,
    ) -> None:
        engine = _make_engine()

        step = {
            "poster": "alice",
            "title": "Do a thing",
            "spec": "Do a thing",
            "reward": 50,
            "bidding_deadline_seconds": 3600,
            "acceptance_window_seconds": 300,
            "execution_deadline_seconds": 7200,
            "review_deadline_seconds": 3600,
        }
        with pytest.raises(RuntimeError, match="explicit durable run"):
            await engine._do_post_task(MagicMock(), step)

    @pytest.mark.asyncio
    async def test_do_trigger_ruling_uses_config_court_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dispute = {
            "action": "dispute",
            "poster": "alice",
            "task_ref": "task",
            "reason": REASON,
        }
        step = {"action": "trigger_ruling", "agent": "alice", "task_ref": "task"}
        engine = _durable_engine([dispute, step])
        engine._config = _make_config()
        engine.platform = MagicMock(agent_id="a-platform")
        monkeypatch.setattr(clients, "get_task", AsyncMock(return_value=_task("submitted", 2)))
        monkeypatch.setattr(
            clients,
            "dispute_task",
            AsyncMock(
                side_effect=lambda *_args, **kwargs: _response(
                    "dispute", kwargs["operation_id"], 2, pending=False
                )
            ),
        )
        await engine._execute_step(MagicMock(), dispute)
        mock_ruling = AsyncMock(
            return_value={
                "dispute_id": "clm-demo",
                "task_id": "t-00000000-0000-4000-8000-000000000001",
                "status": "ruled",
                "worker_pct": 50,
                "ruling_summary": "Split",
                "ruled_at": "2026-10-22T00:00:00Z",
            }
        )
        monkeypatch.setattr(
            clients,
            "get_dispute",
            AsyncMock(
                return_value={
                    "dispute_id": "clm-demo",
                    "task_id": "t-00000000-0000-4000-8000-000000000001",
                    "status": "rebuttal_pending",
                }
            ),
        )
        monkeypatch.setattr(clients, "trigger_ruling", mock_ruling)
        await engine._execute_step(MagicMock(), step)

        mock_ruling.assert_awaited_once()
        assert mock_ruling.call_args[0][3] == "http://court.test"
