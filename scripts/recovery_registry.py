"""Capture registry identity facts without reading the live database or using names."""

from __future__ import annotations

import base64
import binascii
from typing import cast
from urllib.parse import quote

import httpx


class RegistrySnapshotError(RuntimeError):
    """A registry read cannot establish trustworthy source identity evidence."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _get(client: httpx.Client, path: str) -> dict[str, object]:
    try:
        response = client.get(path, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RegistrySnapshotError("registry_unavailable") from exc
    try:
        value = response.json()
    except ValueError as exc:
        raise RegistrySnapshotError("invalid_registry_response") from exc
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise RegistrySnapshotError("invalid_registry_response")
    return cast("dict[str, object]", value)


def _agent_ids(client: httpx.Client) -> frozenset[str]:
    records = _get(client, "/agents").get("agents")
    if not isinstance(records, list):
        raise RegistrySnapshotError("invalid_registry_response")
    ids: set[str] = set()
    for record in records:
        agent_id = record.get("agent_id") if isinstance(record, dict) else None
        if (
            type(agent_id) is not str
            or not agent_id
            or agent_id in {".", ".."}
            or any(
                character.isspace() or ord(character) < 32 or character in "/\\"
                for character in agent_id
            )
            or agent_id in ids
        ):
            raise RegistrySnapshotError("invalid_registry_response")
        ids.add(agent_id)
    return frozenset(ids)


def _public_key(value: object) -> str:
    if type(value) is not str or not value.startswith("ed25519:"):
        raise RegistrySnapshotError("invalid_registry_response")
    encoded = value[len("ed25519:") :]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RegistrySnapshotError("invalid_registry_response") from exc
    if len(raw) != 32 or raw == bytes(32) or base64.b64encode(raw).decode("ascii") != encoded:
        raise RegistrySnapshotError("invalid_registry_response")
    return value


def capture_registry_bindings(client: httpx.Client, *, max_attempts: int) -> dict[str, str]:
    """Return canonical public-key -> agent-ID bindings from list/detail/list reads.

    The caller owns the client's explicit endpoint and timeout. A failed HTTP read
    is never evidence of an empty registry. Registration changes restart the whole
    capture up to the caller's explicit bound; display names are never consulted.
    """
    if type(max_attempts) is not int or max_attempts <= 0:
        raise ValueError("max_attempts must be a positive strict integer")
    for _attempt in range(max_attempts):
        before = _agent_ids(client)
        bindings: dict[str, str] = {}
        for agent_id in sorted(before):
            record = _get(client, f"/agents/{quote(agent_id, safe='')}")
            if record.get("agent_id") != agent_id:
                raise RegistrySnapshotError("invalid_registry_response")
            public_key = _public_key(record.get("public_key"))
            if public_key in bindings:
                raise RegistrySnapshotError("invalid_registry_response")
            bindings[public_key] = agent_id
        if _agent_ids(client) == before:
            return bindings
    raise RegistrySnapshotError("registry_changed_during_capture")
