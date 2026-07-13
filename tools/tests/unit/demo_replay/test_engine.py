"""Unit tests for demo_replay.engine — scenario step dispatch with mocked clients.

Covers item 6(b) (an unknown scenario action becomes a hard error instead of
being silently skipped) and item 7 (dispatch is exercised with mocked
clients rather than a live stack, and every client call uses the
config-resolved URL, never a hardcoded one).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from demo_replay import clients
from demo_replay.config import PlatformConfig
from demo_replay.engine import ReplayEngine, UnknownActionError


def _make_config() -> PlatformConfig:
    return PlatformConfig(
        identity_url="http://identity.test",
        bank_url="http://bank.test",
        task_board_url="http://taskboard.test",
        reputation_url="http://reputation.test",
        court_url="http://court.test",
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
        engine = _make_engine()
        engine._refs["login_task"] = "t-1"
        mock_submit = AsyncMock(return_value={"feedback_id": "f-1"})
        monkeypatch.setattr(clients, "submit_feedback", mock_submit)

        step = {
            "action": "feedback",
            "agent": "alice",
            "task_ref": "login_task",
            "to_agent_id": "bob",
            "category": "spec_quality",
            "rating": "satisfied",
            "comment": "nice work",
        }
        await engine._do_feedback(MagicMock(), step)

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

        await engine._do_register(MagicMock(), {"agent": "alice"})

        mock_register.assert_awaited_once()
        assert mock_register.call_args[0][2] == "http://identity.test"
        mock_create_account.assert_awaited_once()
        assert mock_create_account.call_args[0][3] == "http://bank.test"

    @pytest.mark.asyncio
    async def test_do_post_task_uses_config_task_board_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _make_engine()
        mock_post_task = AsyncMock(return_value={"task_id": "t-1"})
        monkeypatch.setattr(clients, "post_task", mock_post_task)

        step = {"poster": "alice", "title": "Do a thing", "reward": 50}
        await engine._do_post_task(MagicMock(), step)

        mock_post_task.assert_awaited_once()
        assert mock_post_task.call_args.kwargs["task_board_url"] == "http://taskboard.test"

    @pytest.mark.asyncio
    async def test_do_trigger_ruling_uses_config_court_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine = _make_engine()
        engine._refs["t-1"] = "t-1"
        engine._disputes["t-1"] = "d-1"
        engine.platform = MagicMock(agent_id="a-platform")
        mock_ruling = AsyncMock(return_value={"worker_pct": 50})
        monkeypatch.setattr(clients, "trigger_ruling", mock_ruling)

        step = {"agent": "alice", "task_ref": "t-1"}
        await engine._do_trigger_ruling(MagicMock(), step)

        mock_ruling.assert_awaited_once()
        assert mock_ruling.call_args[0][3] == "http://court.test"
