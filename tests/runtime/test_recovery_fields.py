"""Recovery binding discovery uses the paths each real consumer actually resolves."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from scripts import recovery_fields
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from service_commons.recovery_contract import BindingKind, PathResolutionBase

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> dict[str, object]:
    content = yaml.safe_load(path.read_bytes())
    assert isinstance(content, dict)
    return content


def _fields(config_id: str, config: dict[str, object], path: Path, cwd: Path):
    return recovery_fields.configuration_fields(
        config_id,
        config,
        config_path=path,
        process_cwd=cwd,
        agents_package_root=REPO_ROOT / "agents",
    )


def test_every_generated_runtime_binding_is_enumerated(tmp_path: Path) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path)
    expected = {
        "db-gateway": {
            "database.path",
            "database.schema_path",
            "recovery.maintenance_lock_path",
            "recovery.backup_root",
            "board_authority.platform_public_key_path",
            *{
                f"auth.trusted_callers.gateway-{name}.public_key_path"
                for name in ("identity", "central-bank", "task-board", "reputation", "court")
            },
        },
        "identity": {"db_gateway.url", "db_gateway.signing.private_key_path"},
        "central-bank": {
            "identity.base_url",
            "platform.agent_config_path",
            "db_gateway.url",
            "db_gateway.signing.private_key_path",
        },
        "task-board": {
            "database.path",
            "identity.base_url",
            "central_bank.base_url",
            "platform.agent_config_path",
            "assets.storage_path",
            "db_gateway.url",
            "db_gateway.signing.private_key_path",
            "recovery.maintenance_lock_path",
        },
        "reputation": {
            "identity.base_url",
            "platform.agent_config_path",
            "db_gateway.url",
            "db_gateway.signing.private_key_path",
        },
        "court": {
            "platform.agent_config_path",
            "db_gateway.url",
            "db_gateway.signing.private_key_path",
        },
        "ui": {
            "database.path",
            "frontend.web_root",
            "browser_security.allowed_origins",
            "user_agent.agent_config_path",
            "recovery.maintenance_lock_path",
        },
    }
    try:
        for service in SERVICE_ORDER:
            path = runtime.config_paths[service]
            fields = _fields(service, _read(path), path, REPO_ROOT / "services" / service)
            actual = {".".join(str(part) for part in item.field_path) for item in fields}
            assert actual == expected[service] | {"server.port", "logging.directory"}
            assert len(actual) == len(fields)
        agent_config = _read(runtime.agent_config_path)
        fields = _fields("agents", agent_config, runtime.agent_config_path, REPO_ROOT / "agents")
        actual = {".".join(str(part) for part in item.field_path) for item in fields}
        assert actual == {
            "platform.identity_url",
            "platform.bank_url",
            "platform.task_board_url",
            "platform.reputation_url",
            "platform.court_url",
            "data.keys_dir",
            "data.roster_path",
            "runtime_state.directory",
            "task_feeder.tasks_file",
            "workers.mathbot.llm.base_url",
            "workers.mathbot_openai.llm.base_url",
            "workers.mathbot_mistral.llm.base_url",
        }
    finally:
        runtime.stop()


def test_relative_paths_use_consumer_anchors_and_urls_stay_literal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated = tmp_path / "recovery-command"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    agent_config = _read(REPO_ROOT / "agents" / "config.yaml")
    fields = _fields("agents", agent_config, tmp_path / "configs" / "agents.yaml", tmp_path / "cwd")
    by_field = {item.field_path: item for item in fields}
    assert by_field[("data", "keys_dir")].resolved_source_path == tmp_path / "data" / "keys"
    assert by_field[("data", "keys_dir")].resolution_base is PathResolutionBase.CONFIG_PARENT
    assert (
        by_field[("task_feeder", "tasks_file")].resolved_source_path
        == tmp_path / "data" / "math_tasks.jsonl"
    )
    assert (
        by_field[("task_feeder", "tasks_file")].resolution_base is PathResolutionBase.CONFIG_PARENT
    )
    provider = by_field[("workers", "mathbot_openai", "llm", "base_url")]
    assert provider.binding_kind is BindingKind.NETWORK
    assert provider.captured_value == "https://api.openai.com/v1"
    assert provider.resolved_source_path is None
    assert provider.resolution_base is None

    ui_config = _read(REPO_ROOT / "services" / "ui" / "config.yaml")
    fields = _fields("ui", ui_config, tmp_path / "configs" / "ui.yaml", tmp_path / "service-cwd")
    by_field = {item.field_path: item for item in fields}
    assert (
        by_field[("user_agent", "agent_config_path")].resolved_source_path
        == tmp_path.parent / "agents" / "config.yaml"
    )
    assert (
        by_field[("user_agent", "agent_config_path")].resolution_base
        is PathResolutionBase.PROCESS_CWD
    )
    assert (
        by_field[("recovery", "maintenance_lock_path")].resolved_source_path
        == tmp_path.parent / "data" / "maintenance.lock"
    )
    assert (
        by_field[("recovery", "maintenance_lock_path")].resolution_base
        is PathResolutionBase.CONFIG_PARENT
    )


def test_court_provider_fields_expand_concrete_list_indexes(tmp_path: Path) -> None:
    config = _read(REPO_ROOT / "services" / "court" / "config.yaml")
    config["judges"]["judges"] = [
        {"id": "local", "api_base": "http://127.0.0.1:1234/v1"},
        {"id": "mock", "provider": "mock"},
        {"id": "remote", "api_base": "https://judge.example/v1"},
    ]
    fields = _fields("court", config, tmp_path / "court.yaml", tmp_path)
    providers = [item for item in fields if item.field_path[0] == "judges"]
    assert [item.field_path for item in providers] == [
        ("judges", "judges", 0, "api_base"),
        ("judges", "judges", 2, "api_base"),
    ]
    assert [item.captured_value for item in providers] == [
        "http://127.0.0.1:1234/v1",
        "https://judge.example/v1",
    ]
    assert all(item.resolved_source_path is None for item in providers)


@pytest.mark.parametrize("bad_port", [True, "8008", 8008.0])
def test_binding_values_cannot_be_silently_coerced(tmp_path: Path, bad_port: object) -> None:
    config = _read(REPO_ROOT / "services" / "ui" / "config.yaml")
    config["server"]["port"] = bad_port
    with pytest.raises(ValueError, match=r"server\.port"):
        _fields("ui", config, tmp_path / "ui.yaml", tmp_path)


def test_captured_origins_do_not_alias_mutable_configuration(tmp_path: Path) -> None:
    config = _read(REPO_ROOT / "services" / "ui" / "config.yaml")
    expected = tuple(config["browser_security"]["allowed_origins"])
    fields = _fields("ui", config, tmp_path / "ui.yaml", tmp_path)
    origins = next(item for item in fields if item.binding_kind is BindingKind.ORIGINS)
    config["browser_security"]["allowed_origins"].append("https://changed.example")
    assert origins.captured_value == expected


def test_missing_known_field_and_unknown_config_fail_without_input_dump(tmp_path: Path) -> None:
    config = _read(REPO_ROOT / "services" / "ui" / "config.yaml")
    original = copy.deepcopy(config)
    del config["user_agent"]["agent_config_path"]
    config["secret"] = "DO-NOT-DUMP-THIS"
    with pytest.raises(ValueError, match=r"user_agent\.agent_config_path") as exc:
        _fields("ui", config, tmp_path / "ui.yaml", tmp_path)
    assert "DO-NOT-DUMP-THIS" not in str(exc.value)
    with pytest.raises(ValueError, match="Unknown recovery config"):
        _fields("made-up-service", original, tmp_path / "ui.yaml", tmp_path)
