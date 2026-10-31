"""Explicit configuration for caller-owned durable runtime state."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


def _canonical_directory(path: Path) -> Path:
    """Reject unsafe lexical components before resolving parent traversals."""
    if not path.is_absolute() or not path.name or path == Path(path.anchor):
        raise ValueError("Runtime directory must be absolute and cannot be the filesystem root")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ValueError("Runtime directory must not contain a symlink")
        if current.exists() and not current.is_dir():
            raise ValueError("Runtime directory components must be directories")
    resolved = path.resolve()
    # Recheck the normalized result; admission to the journal also verifies paths
    # with no-follow descriptors and a held lease before writing any record.
    for directory in (resolved, *resolved.parents):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("Runtime directory is unsafe")
    return resolved


class RuntimeStateConfig(BaseModel):
    """Required immutable state scope and bounded journal settings."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    environment_id: str
    directory: Path
    diagnostic_limit: int = Field(ge=1)
    max_record_bytes: int = Field(ge=1)

    @field_validator("environment_id")
    @classmethod
    def _nonblank_environment(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("environment_id must not be blank")
        return value

    @field_validator("directory")
    @classmethod
    def _absolute_directory(cls, value: Path) -> Path:
        return _canonical_directory(value)


class _FileSettings(BaseModel):
    """Read the runtime section without owning unrelated agent configuration."""

    model_config = ConfigDict(extra="ignore", strict=True)

    runtime_state: RuntimeStateConfig


def _require_config_path(value: object) -> Path:
    if not isinstance(value, Path):
        raise TypeError("config_path must be a Path")
    return value


def load_runtime_state_config(config_path: Path) -> RuntimeStateConfig:
    """Resolve the required runtime section relative to the explicit config file."""
    config_path = _require_config_path(config_path)
    config_directory = _canonical_directory(config_path.absolute().parent)
    if config_path.is_symlink():
        raise ValueError("Runtime config file must not be a symlink")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Runtime config must be a mapping")
    section = raw.get("runtime_state")
    if isinstance(section, dict) and isinstance(section.get("directory"), str):
        directory_text = section["directory"]
        if not directory_text.strip():
            raise ValueError("Runtime directory must not be blank")
        path = Path(directory_text)
        if not path.is_absolute():
            path = config_directory / path
        section = {**section, "directory": _canonical_directory(path)}
        raw = {**raw, "runtime_state": section}
    return _FileSettings.model_validate(raw).runtime_state
