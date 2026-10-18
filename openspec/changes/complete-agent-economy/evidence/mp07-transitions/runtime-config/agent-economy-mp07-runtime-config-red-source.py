"""T-181 strict runtime-state configuration contract."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

pytestmark = pytest.mark.unit

_MODULE_NAME = "base_agent.runtime_config"
_REQUIRED_EXPORTS = {"RuntimeStateConfig", "load_runtime_state_config"}
_module_spec = importlib.util.find_spec(_MODULE_NAME)
_runtime_config: Any | None = (
    importlib.import_module(_MODULE_NAME) if _module_spec is not None else None
)
_missing_exports = (
    sorted(_REQUIRED_EXPORTS.difference(dir(_runtime_config)))
    if _runtime_config is not None
    else []
)

_ENVIRONMENT_ID = "  Prod-West / Blue  "
_DIAGNOSTIC_LIMIT = 8
_MAX_RECORD_BYTES = 65_536


def _values(directory: Path, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "environment_id": _ENVIRONMENT_ID,
        "directory": directory,
        "diagnostic_limit": _DIAGNOSTIC_LIMIT,
        "max_record_bytes": _MAX_RECORD_BYTES,
    }
    values.update(overrides)
    return values


def _write_config(
    config_path: Path,
    runtime_state: object,
    *,
    top_level_extra: bool = True,
) -> Path:
    document: dict[str, object] = {"runtime_state": runtime_state}
    if top_level_extra:
        document["platform"] = {"identity_url": "https://identity.example"}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(document, sort_keys=False))
    return config_path


@pytest.fixture(autouse=True)
def _required_runtime_config_interface(request: pytest.FixtureRequest) -> None:
    """Keep downstream cases required while one capability assertion owns the RED."""
    if request.node.name == "test_runtime_config_interface_is_available":
        return
    assert _runtime_config is not None, (
        "Blocked by absent T-181 interface: base_agent.runtime_config is required"
    )
    assert _missing_exports == [], (
        f"Blocked by incomplete T-181 runtime-config interface: {_missing_exports}"
    )


def test_runtime_config_interface_is_available() -> None:
    assert _runtime_config is not None, (
        "T-181 capability missing: base_agent.runtime_config has not been implemented"
    )
    assert _missing_exports == [], (
        f"T-181 runtime-config interface is incomplete: {_missing_exports}"
    )

    signature = inspect.signature(_runtime_config.load_runtime_state_config)
    assert tuple(signature.parameters) == ("config_path",)
    parameter = signature.parameters["config_path"]
    assert parameter.default is inspect.Parameter.empty


def test_valid_model_preserves_exact_environment_and_canonical_directory(
    tmp_path: Path,
) -> None:
    assert _runtime_config is not None
    directory = tmp_path / "runtime-state"
    config = _runtime_config.RuntimeStateConfig(**_values(directory))
    assert config.environment_id == _ENVIRONMENT_ID
    assert config.directory == directory.resolve()
    assert config.diagnostic_limit == _DIAGNOSTIC_LIMIT
    assert config.max_record_bytes == _MAX_RECORD_BYTES


def test_runtime_config_is_frozen(tmp_path: Path) -> None:
    assert _runtime_config is not None
    config = _runtime_config.RuntimeStateConfig(**_values(tmp_path / "state"))
    with pytest.raises(ValidationError):
        config.environment_id = "replacement"


def test_model_rejects_relative_directory_without_config_anchor() -> None:
    assert _runtime_config is not None
    with pytest.raises(ValidationError, match="directory"):
        _runtime_config.RuntimeStateConfig(**_values(Path("runtime-state")))


def test_model_rejects_symlinked_absolute_directory(tmp_path: Path) -> None:
    assert _runtime_config is not None
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-runtime"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValidationError, match=r"symlink|directory"):
        _runtime_config.RuntimeStateConfig(**_values(link))


@pytest.mark.parametrize(
    "missing_field",
    ["environment_id", "directory", "diagnostic_limit", "max_record_bytes"],
)
def test_model_requires_every_runtime_state_field(
    tmp_path: Path,
    missing_field: str,
) -> None:
    assert _runtime_config is not None
    values = _values(tmp_path / "state")
    del values[missing_field]
    with pytest.raises(ValidationError, match=missing_field):
        _runtime_config.RuntimeStateConfig(**values)


def test_model_rejects_extra_runtime_state_field(tmp_path: Path) -> None:
    assert _runtime_config is not None
    with pytest.raises(ValidationError, match="unexpected"):
        _runtime_config.RuntimeStateConfig(**_values(tmp_path / "state", unexpected="not-allowed"))


@pytest.mark.parametrize("environment_id", ["", " ", "\t", "\n"])
def test_environment_id_rejects_blank_values(
    tmp_path: Path,
    environment_id: str,
) -> None:
    assert _runtime_config is not None
    with pytest.raises(ValidationError, match="environment_id"):
        _runtime_config.RuntimeStateConfig(
            **_values(tmp_path / "state", environment_id=environment_id)
        )


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("environment_id", 1),
        ("environment_id", ["production"]),
        ("directory", 1),
        ("directory", ["state"]),
        ("diagnostic_limit", "8"),
        ("diagnostic_limit", 8.0),
        ("max_record_bytes", "65536"),
        ("max_record_bytes", 65_536.0),
    ],
)
def test_model_rejects_wrong_field_types(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    assert _runtime_config is not None
    with pytest.raises(ValidationError, match=field):
        _runtime_config.RuntimeStateConfig(**_values(tmp_path / "state", **{field: invalid_value}))


@pytest.mark.parametrize("field", ["diagnostic_limit", "max_record_bytes"])
@pytest.mark.parametrize("invalid_value", [True, False])
def test_integer_limits_reject_booleans(
    tmp_path: Path,
    field: str,
    invalid_value: bool,
) -> None:
    assert _runtime_config is not None
    with pytest.raises(ValidationError, match=field):
        _runtime_config.RuntimeStateConfig(**_values(tmp_path / "state", **{field: invalid_value}))


@pytest.mark.parametrize("field", ["diagnostic_limit", "max_record_bytes"])
@pytest.mark.parametrize("invalid_value", [0, -1])
def test_integer_limits_must_be_positive(
    tmp_path: Path,
    field: str,
    invalid_value: int,
) -> None:
    assert _runtime_config is not None
    with pytest.raises(ValidationError, match=field):
        _runtime_config.RuntimeStateConfig(**_values(tmp_path / "state", **{field: invalid_value}))


def test_integer_limits_accept_exact_positive_boundary(tmp_path: Path) -> None:
    assert _runtime_config is not None
    config = _runtime_config.RuntimeStateConfig(
        **_values(
            tmp_path / "state",
            diagnostic_limit=1,
            max_record_bytes=1,
        )
    )
    assert config.diagnostic_limit == 1
    assert config.max_record_bytes == 1


def test_loader_requires_top_level_runtime_state_section(tmp_path: Path) -> None:
    assert _runtime_config is not None
    config_path = tmp_path / "config.yaml"
    config_path.write_text("platform:\n  identity_url: https://identity.example\n")
    with pytest.raises(ValidationError, match="runtime_state"):
        _runtime_config.load_runtime_state_config(config_path)


def test_loader_requires_path_object_for_explicit_config_file(tmp_path: Path) -> None:
    assert _runtime_config is not None
    config_path = _write_config(
        tmp_path / "config.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": "state",
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    with pytest.raises(TypeError, match="Path"):
        _runtime_config.load_runtime_state_config(str(config_path))


@pytest.mark.parametrize("document", ["", "[]\n", "runtime_state\n"])
def test_loader_rejects_non_mapping_document(tmp_path: Path, document: str) -> None:
    assert _runtime_config is not None
    config_path = tmp_path / "config.yaml"
    config_path.write_text(document)
    with pytest.raises(ValueError, match=r"config|mapping|runtime_state"):
        _runtime_config.load_runtime_state_config(config_path)


@pytest.mark.parametrize("section", [None, [], "state", 1, True])
def test_loader_rejects_non_mapping_runtime_state_section(
    tmp_path: Path,
    section: object,
) -> None:
    assert _runtime_config is not None
    config_path = _write_config(tmp_path / "config.yaml", section)
    with pytest.raises((ValueError, ValidationError), match="runtime_state"):
        _runtime_config.load_runtime_state_config(config_path)


@pytest.mark.parametrize(
    "missing_field",
    ["environment_id", "directory", "diagnostic_limit", "max_record_bytes"],
)
def test_loader_requires_every_yaml_field(
    tmp_path: Path,
    missing_field: str,
) -> None:
    assert _runtime_config is not None
    values = _values(Path("state"))
    del values[missing_field]
    if "directory" in values:
        values["directory"] = str(values["directory"])
    config_path = _write_config(tmp_path / "config.yaml", values)
    with pytest.raises(ValidationError, match=missing_field):
        _runtime_config.load_runtime_state_config(config_path)


def test_loader_rejects_extra_yaml_field(tmp_path: Path) -> None:
    assert _runtime_config is not None
    values = _values(Path("state"), unexpected="not-allowed")
    values["directory"] = "state"
    config_path = _write_config(tmp_path / "config.yaml", values)
    with pytest.raises(ValidationError, match="unexpected"):
        _runtime_config.load_runtime_state_config(config_path)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("environment_id", True),
        ("environment_id", 7),
        ("directory", True),
        ("directory", 7),
        ("diagnostic_limit", True),
        ("diagnostic_limit", "8"),
        ("max_record_bytes", False),
        ("max_record_bytes", "65536"),
    ],
)
def test_loader_rejects_yaml_coercions(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    assert _runtime_config is not None
    values = _values(Path("state"), **{field: invalid_value})
    if field != "directory":
        values["directory"] = "state"
    config_path = _write_config(tmp_path / "config.yaml", values)
    with pytest.raises(ValidationError, match=field):
        _runtime_config.load_runtime_state_config(config_path)


def test_relative_directory_is_resolved_from_explicit_config_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _runtime_config is not None
    config_directory = tmp_path / "configuration"
    config_path = _write_config(
        config_directory / "agents.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": "runtime/state",
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    unrelated_cwd = tmp_path / "working-directory"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    config = _runtime_config.load_runtime_state_config(config_path)

    assert config.directory == (config_directory / "runtime/state").resolve()
    assert config.environment_id == _ENVIRONMENT_ID


def test_loader_returns_canonical_absolute_directory(tmp_path: Path) -> None:
    assert _runtime_config is not None
    directory = tmp_path / "one" / ".." / "state"
    config_path = _write_config(
        tmp_path / "config.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": str(directory),
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    config = _runtime_config.load_runtime_state_config(config_path)
    assert config.directory == (tmp_path / "state").resolve()
    assert config.directory.is_absolute()


def test_loading_nonexistent_directory_does_not_create_it(tmp_path: Path) -> None:
    assert _runtime_config is not None
    expected = tmp_path / "future" / "runtime-state"
    config_path = _write_config(
        tmp_path / "config.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": "future/runtime-state",
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    config = _runtime_config.load_runtime_state_config(config_path)
    assert config.directory == expected
    assert not expected.exists()


@pytest.mark.parametrize("directory", ["", " ", "\t"])
def test_loader_rejects_blank_directory(tmp_path: Path, directory: str) -> None:
    assert _runtime_config is not None
    config_path = _write_config(
        tmp_path / "config.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": directory,
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    with pytest.raises((ValueError, ValidationError), match="directory"):
        _runtime_config.load_runtime_state_config(config_path)


def test_loader_rejects_directory_that_is_an_existing_file(tmp_path: Path) -> None:
    assert _runtime_config is not None
    regular_file = tmp_path / "not-a-directory"
    regular_file.write_text("ordinary file")
    config_path = _write_config(
        tmp_path / "config.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": str(regular_file),
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    with pytest.raises((ValueError, ValidationError), match="directory"):
        _runtime_config.load_runtime_state_config(config_path)


def test_loader_rejects_directory_that_is_a_symlink(tmp_path: Path) -> None:
    assert _runtime_config is not None
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-runtime"
    link.symlink_to(target, target_is_directory=True)
    config_path = _write_config(
        tmp_path / "config.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": str(link),
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    with pytest.raises((ValueError, ValidationError), match=r"symlink|directory"):
        _runtime_config.load_runtime_state_config(config_path)


def test_loader_rejects_symlinked_directory_ancestor(tmp_path: Path) -> None:
    assert _runtime_config is not None
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-parent"
    link.symlink_to(target, target_is_directory=True)
    config_path = _write_config(
        tmp_path / "config.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": "linked-parent/runtime-state",
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    with pytest.raises((ValueError, ValidationError), match=r"symlink|directory"):
        _runtime_config.load_runtime_state_config(config_path)


def test_symlink_component_is_rejected_before_dotdot_resolution(tmp_path: Path) -> None:
    assert _runtime_config is not None
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-parent"
    link.symlink_to(target, target_is_directory=True)
    config_path = _write_config(
        tmp_path / "config.yaml",
        {
            "environment_id": _ENVIRONMENT_ID,
            "directory": "linked-parent/../apparently-safe",
            "diagnostic_limit": _DIAGNOSTIC_LIMIT,
            "max_record_bytes": _MAX_RECORD_BYTES,
        },
    )
    with pytest.raises((ValueError, ValidationError), match=r"symlink|directory"):
        _runtime_config.load_runtime_state_config(config_path)
