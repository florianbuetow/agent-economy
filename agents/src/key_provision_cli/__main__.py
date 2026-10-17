"""Provision, validate, or explicitly adopt configured local agent keys."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel, ConfigDict, ValidationError
from service_auth.gateway import GatewayTrustConfig  # noqa: TC002 - Pydantic runtime field
from service_auth.signing import (
    KeyMaterialError,
    adopt_keypair_permissions,
    generate_keypair,
    keypair_fingerprint,
    load_keypair,
)

from base_agent.factory import AgentFactory

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_Action = Literal["provision", "check", "adopt-permissions"]


class _GatewayFileConfig(BaseModel):
    """Gateway file surface needed by key administration."""

    model_config = ConfigDict(extra="ignore", strict=True)
    auth: GatewayTrustConfig


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("provision", "check", "adopt-permissions"),
        help="Explicit administrative key-store action.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Agent config path; otherwise use AGENT_CONFIG_PATH or agents/config.yaml.",
    )
    parser.add_argument(
        "--gateway-config",
        type=Path,
        help="Optional Database Gateway config whose trusted caller pairs are administered.",
    )
    return parser.parse_args(argv)


def _load_gateway_pairs(config_path: Path) -> tuple[tuple[str, Path], ...]:
    try:
        raw = yaml.safe_load(config_path.read_text())
        config = _GatewayFileConfig.model_validate(raw)
    except (OSError, ValidationError, yaml.YAMLError) as exc:
        msg = f"Invalid Gateway configuration {config_path}: {exc}"
        raise KeyMaterialError("invalid_gateway_config", msg) from exc

    pairs: list[tuple[str, Path]] = []
    canonical_paths: set[Path] = set()
    for kid, caller in sorted(config.auth.trusted_callers.items()):
        public_path = caller.public_key_path
        if not public_path.is_absolute():
            public_path = config_path.parent / public_path
        public_path = Path(
            os.path.abspath(public_path)  # noqa: PTH100 - normalization must not follow symlinks
        )
        current = Path(public_path.anchor)
        for component in public_path.parts[1:]:
            current /= component
            try:
                component_stat = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(component_stat.st_mode):
                msg = f"Unsafe Gateway public key path contains a symlink: {current}"
                raise KeyMaterialError("unsafe_path", msg)
        if public_path in canonical_paths:
            msg = f"Duplicate Gateway public key path after resolution: {public_path}"
            raise KeyMaterialError("invalid_gateway_config", msg)
        canonical_paths.add(public_path)
        pairs.append((kid, public_path))
    return tuple(pairs)


def _fingerprint(public_key: Ed25519PublicKey) -> str:
    raw_public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw_public).hexdigest()


def _configured_targets(
    factory: AgentFactory,
    gateway_pairs: tuple[tuple[str, Path], ...],
) -> tuple[dict[str, Path], dict[str, Path]]:
    agent_targets = factory.configured_key_targets()
    gateway_targets = dict(gateway_pairs)
    shared_handles = set(agent_targets) & set(gateway_targets)
    if shared_handles:
        handle = sorted(shared_handles)[0]
        msg = f"Gateway caller handle collides with economic roster handle: {handle!r}"
        raise KeyMaterialError("invalid_gateway_config", msg)

    path_owners: dict[Path, str] = {}
    for handle, public_path in (*agent_targets.items(), *gateway_targets.items()):
        canonical_path = public_path.resolve()
        owner = path_owners.get(canonical_path)
        if owner is not None:
            msg = f"Configured public key path is shared by {owner!r} and {handle!r}"
            raise KeyMaterialError("invalid_gateway_config", msg)
        path_owners[canonical_path] = handle
    return agent_targets, gateway_targets


def _preflight_targets(
    agent_targets: dict[str, Path],
    gateway_targets: dict[str, Path],
    *,
    action: _Action,
) -> dict[str, str]:
    allow_absent = action == "provision"
    allow_legacy_private_mode = action == "adopt-permissions"
    fingerprints: dict[str, str] = {}
    for handle, public_path in (*agent_targets.items(), *gateway_targets.items()):
        try:
            fingerprints[handle] = keypair_fingerprint(
                handle,
                public_path.parent,
                allow_legacy_private_mode=allow_legacy_private_mode,
            )
        except KeyMaterialError as exc:
            if allow_absent and exc.reason == "absent_pair":
                continue
            raise

    fingerprint_owners: dict[str, str] = {}
    for handle in agent_targets:
        fingerprint = fingerprints.get(handle)
        if fingerprint is not None:
            fingerprint_owners.setdefault(fingerprint, handle)
    for handle in gateway_targets:
        fingerprint = fingerprints.get(handle)
        if fingerprint is None:
            continue
        owner = fingerprint_owners.get(fingerprint)
        if owner is not None:
            msg = f"Duplicate key identity fingerprint shared by {owner!r} and {handle!r}"
            raise KeyMaterialError("duplicate_identity", msg)
        fingerprint_owners[fingerprint] = handle
    return fingerprints


def _administer_gateway_pairs(
    pairs: tuple[tuple[str, Path], ...],
    *,
    action: _Action,
) -> dict[str, str]:
    results: dict[str, str] = {}
    for kid, public_path in pairs:
        if action == "provision":
            _private_key, public_key = generate_keypair(kid, public_path.parent)
        elif action == "check":
            _private_key, public_key = load_keypair(kid, public_path.parent)
        else:
            _private_key, public_key = adopt_keypair_permissions(kid, public_path.parent)
        results[kid] = _fingerprint(public_key)
    return results


def main(argv: list[str]) -> int:
    """Run one explicit configured-roster key administration action."""
    args = _parse_args(argv)
    try:
        gateway_pairs = (
            _load_gateway_pairs(args.gateway_config) if args.gateway_config is not None else ()
        )
        factory = AgentFactory(config_path=args.config)
        agent_targets, gateway_targets = _configured_targets(factory, gateway_pairs)
        fingerprints = _preflight_targets(
            agent_targets,
            gateway_targets,
            action=args.action,
        )
        if args.action == "provision":
            factory.provision_configured_keys()
            _administer_gateway_pairs(gateway_pairs, action=args.action)
            result_label = "provisioned"
            fingerprints = _preflight_targets(
                agent_targets,
                gateway_targets,
                action="check",
            )
        elif args.action == "check":
            result_label = "valid"
        else:
            factory.adopt_configured_key_permissions()
            _administer_gateway_pairs(gateway_pairs, action=args.action)
            result_label = "adopted"
            fingerprints = _preflight_targets(
                agent_targets,
                gateway_targets,
                action="check",
            )
    except (KeyMaterialError, OSError, ValidationError, ValueError, yaml.YAMLError) as exc:
        reason = exc.reason if isinstance(exc, KeyMaterialError) else "invalid_config"
        print(
            f"Key administration failed: reason={reason} message={exc}",
            file=sys.stderr,
        )
        if args.action == "check":
            print("Run 'just provision-keys' for an empty key store.", file=sys.stderr)
        else:
            print("See README key recovery instructions; no key was replaced.", file=sys.stderr)
        return 1

    for handle, fingerprint in fingerprints.items():
        print(f"{result_label} handle={handle} fingerprint_sha256={fingerprint}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
