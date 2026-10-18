"""Describe recovery bindings using the actual configuration consumers' anchors.

This module performs no filesystem reads and never substitutes environment values.
The inventory producer supplies the selected YAML mapping and explicit process facts;
the recovery engine subsequently verifies the captured bytes and resource identities.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from service_commons.recovery_contract import BindingKind, CapturedValue, PathResolutionBase

_SERVICES = frozenset(
    {
        "db-gateway",
        "identity",
        "central-bank",
        "task-board",
        "reputation",
        "court",
        "ui",
    }
)
_GATEWAY_CLIENTS = frozenset({"identity", "central-bank", "task-board", "reputation", "court"})
_PLATFORM_CLIENTS = frozenset({"central-bank", "task-board", "reputation", "court"})

type FieldDefinition = tuple[tuple[str | int, ...], BindingKind, PathResolutionBase | None]


def _file(field: str, base: PathResolutionBase) -> FieldDefinition:
    return tuple(field.split(".")), BindingKind.FILESYSTEM, base


def _network(field: str) -> FieldDefinition:
    return tuple(field.split(".")), BindingKind.NETWORK, None


@dataclass(frozen=True, slots=True)
class CapturedField:
    """Immutable source value and its consumer-specific interpretation."""

    field_path: tuple[str | int, ...]
    binding_kind: BindingKind
    captured_value: CapturedValue
    resolution_base: PathResolutionBase | None
    resolved_source_path: Path | None


def _value(config: Mapping[str, object], path: tuple[str | int, ...]) -> object:
    current: object = config
    for segment in path:
        if isinstance(segment, int):
            if not isinstance(current, list) or segment >= len(current):
                raise ValueError(f"Missing recovery field: {'.'.join(map(str, path))}")
            current = current[segment]
        else:
            if not isinstance(current, Mapping) or segment not in current:
                raise ValueError(f"Missing recovery field: {'.'.join(map(str, path))}")
            current = current[segment]
    return current


def _members(config: Mapping[str, object], path: tuple[str | int, ...]) -> Mapping[str, object]:
    value = _value(config, path)
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ValueError(f"Recovery field must be a mapping: {'.'.join(map(str, path))}")
    return value


def _common_fields(config_id: str) -> list[FieldDefinition]:
    definitions: list[FieldDefinition] = []
    cwd = PathResolutionBase.PROCESS_CWD
    parent = PathResolutionBase.CONFIG_PARENT
    if config_id in _SERVICES:
        definitions.append((("server", "port"), BindingKind.PORT, None))
        definitions.append(_file("logging.directory", cwd))
    if config_id in _GATEWAY_CLIENTS:
        definitions.extend(
            (_network("db_gateway.url"), _file("db_gateway.signing.private_key_path", parent))
        )
    if config_id in _PLATFORM_CLIENTS:
        definitions.append(_file("platform.agent_config_path", parent))
    if config_id in {"central-bank", "task-board", "reputation"}:
        definitions.append(_network("identity.base_url"))
    if config_id in {"db-gateway", "task-board", "ui"}:
        definitions.extend(
            (_file("recovery.maintenance_lock_path", parent), _file("database.path", cwd))
        )
    return definitions


def _court_fields(config: Mapping[str, object]) -> list[FieldDefinition]:
    judges = _value(config, ("judges", "judges"))
    if not isinstance(judges, list):
        raise ValueError("Recovery field must be a list: judges.judges")
    fields: list[FieldDefinition] = []
    for index, judge in enumerate(judges):
        if not isinstance(judge, Mapping):
            raise ValueError(f"Recovery field must be a mapping: judges.judges.{index}")
        if "api_base" in judge:
            fields.append((("judges", "judges", index, "api_base"), BindingKind.NETWORK, None))
    return fields


def _specific_fields(config_id: str, config: Mapping[str, object]) -> list[FieldDefinition]:
    definitions: list[FieldDefinition] = []
    cwd = PathResolutionBase.PROCESS_CWD
    parent = PathResolutionBase.CONFIG_PARENT
    if config_id == "agents":
        for name in ("identity_url", "bank_url", "task_board_url", "reputation_url", "court_url"):
            definitions.append(_network(f"platform.{name}"))
        definitions.extend(
            (
                _file("runtime_state.directory", parent),
                _file("data.keys_dir", parent),
                _file("data.roster_path", parent),
                _file("task_feeder.tasks_file", PathResolutionBase.AGENTS_PACKAGE_ROOT),
            )
        )
        for profile in _members(config, ("workers",)):
            definitions.append((("workers", profile, "llm", "base_url"), BindingKind.NETWORK, None))
    elif config_id == "db-gateway":
        definitions.extend(
            (
                _file("database.schema_path", cwd),
                _file("recovery.backup_root", parent),
                _file("board_authority.platform_public_key_path", parent),
            )
        )
        for kid in _members(config, ("auth", "trusted_callers")):
            definitions.append(
                (
                    ("auth", "trusted_callers", kid, "public_key_path"),
                    BindingKind.FILESYSTEM,
                    parent,
                )
            )
    elif config_id == "task-board":
        definitions.extend((_network("central_bank.base_url"), _file("assets.storage_path", cwd)))
    elif config_id == "court":
        definitions.extend(_court_fields(config))
    elif config_id == "ui":
        definitions.extend(
            (_file("frontend.web_root", cwd), _file("user_agent.agent_config_path", cwd))
        )
        definitions.append((("browser_security", "allowed_origins"), BindingKind.ORIGINS, None))
    return definitions


def _typed_value(value: object, kind: BindingKind, *, label: str) -> CapturedValue:
    if kind is BindingKind.PORT:
        if type(value) is not int:
            raise ValueError(f"Recovery field must be a strict integer: {label}")
        return value
    if kind is BindingKind.ORIGINS:
        if not isinstance(value, list) or any(type(item) is not str or not item for item in value):
            raise ValueError(f"Recovery field must be a list of nonempty strings: {label}")
        return tuple(value)
    if type(value) is not str or not value:
        raise ValueError(f"Recovery field must be a nonempty string: {label}")
    return value


def configuration_fields(
    config_id: str,
    config: Mapping[str, object],
    *,
    config_path: Path,
    process_cwd: Path,
    agents_package_root: Path,
) -> tuple[CapturedField, ...]:
    """Expand the exhaustive binding table without guessing paths or coercing values."""
    if config_id not in _SERVICES and config_id != "agents":
        raise ValueError(f"Unknown recovery config: {config_id}")
    for anchor in (config_path, process_cwd, agents_package_root):
        if not anchor.is_absolute() or Path(os.path.normpath(anchor)) != anchor:
            raise ValueError("Recovery configuration anchors must be canonical absolute paths")

    fields: list[CapturedField] = []
    anchors = {
        PathResolutionBase.PROCESS_CWD: process_cwd,
        PathResolutionBase.CONFIG_PARENT: config_path.parent,
        PathResolutionBase.AGENTS_PACKAGE_ROOT: agents_package_root,
    }
    for path, kind, relative_base in _common_fields(config_id) + _specific_fields(
        config_id, config
    ):
        value = _typed_value(_value(config, path), kind, label=".".join(map(str, path)))
        resolved = None
        base = relative_base
        if kind is BindingKind.FILESYSTEM:
            source = Path(cast("str", value))
            if source.is_absolute():
                base = PathResolutionBase.ABSOLUTE
                resolved = Path(os.path.normpath(source))
            else:
                resolved = Path(
                    os.path.normpath(anchors[cast("PathResolutionBase", base)] / source)
                )
        fields.append(CapturedField(path, kind, value, base, resolved))
    return tuple(fields)
