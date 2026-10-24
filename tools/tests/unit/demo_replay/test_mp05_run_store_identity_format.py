"""The durable actor binding accepts the actual Identity wire key format."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from demo_replay.run_store import ActorDefinition, RunEnvironment, RunStore
from demo_replay.wallet import DemoAgent

pytestmark = pytest.mark.unit


def test_real_demo_registration_binds_and_reopens(tmp_path: Path) -> None:
    scenario = {
        "name": "Identity wire format",
        "agents": [{"handle": "poster", "name": "Poster"}],
        "steps": [{"action": "register", "agent": "poster"}],
    }
    environment = RunEnvironment(
        environment_id="identity-format-test",
        identity_url="http://127.0.0.1:8001",
        bank_url="http://127.0.0.1:8002",
        task_board_url="http://127.0.0.1:8003",
        reputation_url="http://127.0.0.1:8004",
        court_url="http://127.0.0.1:8005",
        platform_public_fingerprint_sha256="a" * 64,
    )

    def open_store() -> RunStore:
        return RunStore.open(
            state_directory=tmp_path / "state",
            run_id="run-00000000-0000-4000-8000-000000000551",
            environment=environment,
            scenario_bytes=yaml.safe_dump(scenario).encode(),
            scenario=scenario,
            actor_definitions=(ActorDefinition(handle="poster", name="Poster"),),
            effective_delay_seconds=0.0,
            max_record_bytes=65_536,
            diagnostic_limit=2_048,
        )

    with open_store() as store:
        actor = store.actors["poster"]
        demo = DemoAgent.from_pem("poster", "Poster", actor.private_key_path, 300)
        response: dict[str, object] = {
            "agent_id": "a-poster",
            "name": demo.name,
            "public_key": demo.public_key_string(),
            "registered_at": "2026-10-22T10:15:30Z",
        }
        bound = store.record_registration(actor_handle="poster", response=response)
        assert bound.public_key == demo.public_key_string()
        assert bound.agent_id == "a-poster"
        assert bound.registration == response

    with open_store() as restored:
        assert restored.actors["poster"] == bound
