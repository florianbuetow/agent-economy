"""T-181 recovery inventory includes the caller-owned agent journal."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scripts import recovery_inventory
from scripts.recovery_fields import CapturedField, configuration_fields
from service_commons.recovery_contract import (
    BindingKind,
    CaptureKind,
    PathResolutionBase,
    ResourceKind,
    SourceResourceV1,
)

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[2]


def _agents_config() -> dict[str, object]:
    value = yaml.safe_load((REPO_ROOT / "agents/config.yaml").read_bytes())
    assert isinstance(value, dict)
    return value


def test_runtime_state_directory_is_one_config_parent_filesystem_binding(
    tmp_path: Path,
) -> None:
    config = _agents_config()
    config_path = tmp_path / "configs" / "agents.yaml"
    fields = configuration_fields(
        "agents",
        config,
        config_path=config_path,
        process_cwd=REPO_ROOT / "agents",
        agents_package_root=REPO_ROOT / "agents",
    )
    selected = [field for field in fields if field.field_path == ("runtime_state", "directory")]
    assert len(selected) == 1
    binding = selected[0]
    assert binding.binding_kind is BindingKind.FILESYSTEM
    assert binding.captured_value == "../data/agent-runtime"
    assert binding.resolution_base is PathResolutionBase.CONFIG_PARENT
    assert binding.resolved_source_path == tmp_path / "data" / "agent-runtime"


def test_present_runtime_state_directory_has_copy_inventory_policy(tmp_path: Path) -> None:
    config = _agents_config()
    binding = CapturedField(
        field_path=("runtime_state", "directory"),
        binding_kind=BindingKind.FILESYSTEM,
        captured_value="../data/agent-runtime",
        resolution_base=PathResolutionBase.CONFIG_PARENT,
        resolved_source_path=tmp_path / "data" / "agent-runtime",
    )
    logical_id, kind, capture_kind, required = recovery_inventory._binding_resource(
        "agents",
        binding,
        config,
        {},
    )
    assert logical_id == "agents.runtime_state"
    assert kind is ResourceKind.DIRECTORY
    assert capture_kind is CaptureKind.COPY
    assert required is False


def test_absent_runtime_state_directory_has_explicit_absent_directory_policy(
    tmp_path: Path,
) -> None:
    absent_kind = getattr(CaptureKind, "ABSENT_DIRECTORY", None)
    assert absent_kind is not None, (
        "T-181 capability missing: CaptureKind.ABSENT_DIRECTORY is required"
    )
    absent = tmp_path / "not-created-before-first-use"
    selected = recovery_inventory._resource_capture_kind(
        absent,
        kind=ResourceKind.DIRECTORY,
        required=False,
        capture_kind=CaptureKind.COPY,
        label="agents.runtime_state",
    )
    assert selected is absent_kind
    resource = SourceResourceV1(
        logical_id="agents.runtime_state",
        kind=ResourceKind.DIRECTORY,
        capture_kind=absent_kind,
        capture_root=tmp_path,
        captured_path=absent,
    )
    assert resource.capture_kind is absent_kind
