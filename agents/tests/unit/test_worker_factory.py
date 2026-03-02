"""Unit tests for WorkerFactory."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from base_agent.worker_factory import MathWorkerBundle, WorkerFactory

if TYPE_CHECKING:
    from pathlib import Path


def _write_config(
    tmp_path: Path,
    *,
    workers: dict[str, dict[str, object]] | None = None,
    extra_roster: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Write a minimal config.yaml and roster.yaml for testing."""
    roster_agents: dict[str, dict[str, str]] = {
        "platform": {"name": "Platform", "type": "platform"},
        "testbot": {"name": "Test Bot", "type": "worker"},
    }
    if extra_roster is not None:
        roster_agents.update(extra_roster)

    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(yaml.dump({"agents": roster_agents}))

    config: dict[str, object] = {
        "platform": {
            "identity_url": "http://localhost:8001",
            "bank_url": "http://localhost:8002",
            "task_board_url": "http://localhost:8003",
            "reputation_url": "http://localhost:8004",
            "court_url": "http://localhost:8005",
        },
        "data": {
            "keys_dir": str(tmp_path / "keys"),
            "roster_path": str(roster_path),
        },
    }
    if workers is not None:
        config["workers"] = workers

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    return config_path


def _valid_worker_profile(handle: str = "testbot") -> dict[str, object]:
    return {
        "handle": handle,
        "type": "math_worker",
        "llm": {
            "base_url": "http://localhost:1234/v1",
            "api_key": "test-key",
            "model_id": "test-model",
            "temperature": 0.7,
            "max_tokens": 2048,
        },
        "behavior": {
            "scan_interval_seconds": 10,
            "poll_interval_seconds": 3,
            "max_poll_attempts": 100,
            "error_backoff_seconds": 5,
            "min_reward": 50,
            "max_reward": 10000,
        },
    }


@pytest.mark.unit
class TestWorkerFactory:
    """Tests for WorkerFactory."""

    def test_list_workers(self, tmp_path: Path) -> None:
        config_path = _write_config(
            tmp_path,
            workers={"testbot": _valid_worker_profile()},
        )
        factory = WorkerFactory(config_path=config_path, keys_dir=tmp_path / "keys")
        assert factory.list_workers() == ["testbot"]

    def test_list_workers_sorted(self, tmp_path: Path) -> None:
        config_path = _write_config(
            tmp_path,
            workers={
                "zbot": _valid_worker_profile(handle="zbot"),
                "alpha": _valid_worker_profile(handle="testbot"),
            },
            extra_roster={"zbot": {"name": "Z Bot", "type": "worker"}},
        )
        factory = WorkerFactory(config_path=config_path, keys_dir=tmp_path / "keys")
        assert factory.list_workers() == ["alpha", "zbot"]

    def test_missing_workers_section_raises(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, workers=None)
        with pytest.raises(ValueError, match="workers"):
            WorkerFactory(config_path=config_path, keys_dir=tmp_path / "keys")

    def test_empty_workers_section_raises(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, workers={})
        with pytest.raises(ValueError, match="workers"):
            WorkerFactory(config_path=config_path, keys_dir=tmp_path / "keys")

    def test_handle_not_in_roster_raises(self, tmp_path: Path) -> None:
        config_path = _write_config(
            tmp_path,
            workers={"ghost": _valid_worker_profile(handle="nonexistent")},
        )
        with pytest.raises(KeyError, match="nonexistent"):
            WorkerFactory(config_path=config_path, keys_dir=tmp_path / "keys")

    def test_create_math_worker_returns_bundle(self, tmp_path: Path) -> None:
        config_path = _write_config(
            tmp_path,
            workers={"testbot": _valid_worker_profile()},
        )
        factory = WorkerFactory(config_path=config_path, keys_dir=tmp_path / "keys")
        bundle = factory.create_math_worker("testbot")
        assert isinstance(bundle, MathWorkerBundle)
        assert bundle.agent is not None
        assert bundle.llm is not None
        assert bundle.loop is not None

    def test_create_math_worker_unknown_profile_raises(self, tmp_path: Path) -> None:
        config_path = _write_config(
            tmp_path,
            workers={"testbot": _valid_worker_profile()},
        )
        factory = WorkerFactory(config_path=config_path, keys_dir=tmp_path / "keys")
        with pytest.raises(KeyError, match="unknown"):
            factory.create_math_worker("unknown")

    def test_agent_name_matches_roster(self, tmp_path: Path) -> None:
        config_path = _write_config(
            tmp_path,
            workers={"testbot": _valid_worker_profile()},
        )
        factory = WorkerFactory(config_path=config_path, keys_dir=tmp_path / "keys")
        bundle = factory.create_math_worker("testbot")
        assert bundle.agent.name == "Test Bot"
