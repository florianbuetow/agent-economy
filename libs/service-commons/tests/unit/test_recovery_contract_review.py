"""Regression tests for independently reviewed recovery-contract invariants."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from service_commons.recovery_contract import (
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
    MaintenanceLockIdentityV1,
    RecoveryInventoryV1,
    RegistrationRequirement,
    ResourceKind,
    SourceConfigV1,
    SourceKeypairV1,
    SourceResourceV1,
)

pytestmark = pytest.mark.unit


def _resource(
    logical_id: str,
    path: str,
    *,
    kind: ResourceKind = ResourceKind.FILE,
    capture_kind: CaptureKind = CaptureKind.COPY,
) -> SourceResourceV1:
    return SourceResourceV1(
        logical_id=logical_id,
        kind=kind,
        capture_kind=capture_kind,
        capture_root=Path(path).parent,
        captured_path=Path(path),
    )


def _key_resources(handle: str) -> tuple[SourceResourceV1, SourceResourceV1]:
    return (
        _resource(
            f"{handle}-private",
            f"/source/keys/{handle}.key",
            capture_kind=CaptureKind.KEYPAIR,
        ),
        _resource(
            f"{handle}-public",
            f"/source/keys/{handle}.pub",
            capture_kind=CaptureKind.KEYPAIR,
        ),
    )


def _keypair(
    handle: str,
    fingerprint: str,
    *,
    role: KeypairRole = KeypairRole.ECONOMIC,
    agent_id: str | None = "agent-1",
) -> SourceKeypairV1:
    is_gateway = role is KeypairRole.GATEWAY
    return SourceKeypairV1(
        handle=handle,
        role=role,
        private_resource_id=f"{handle}-private",
        public_resource_id=f"{handle}-public",
        expected_public_fingerprint_sha256=fingerprint,
        registration_requirement=(
            RegistrationRequirement.FORBIDDEN if is_gateway else RegistrationRequirement.REQUIRED
        ),
        captured_registration_state=(
            CapturedRegistrationState.UNREGISTERED
            if is_gateway
            else CapturedRegistrationState.REGISTERED
        ),
        expected_registered_agent_id=None if is_gateway else agent_id,
    )


def _inventory(
    *,
    resources: tuple[SourceResourceV1, ...] = (),
    keypairs: tuple[SourceKeypairV1, ...] = (),
    maintenance_lock_path: Path = Path("/source/run/maintenance.lock"),
) -> RecoveryInventoryV1:
    return RecoveryInventoryV1(
        contract_version=1,
        runtime_id="runtime-1",
        source_root=Path("/source"),
        agents_package_root=Path("/source/agents/src"),
        maintenance_lock_path=maintenance_lock_path,
        maintenance_lock_identity=MaintenanceLockIdentityV1(device=1, inode=2),
        resources=resources,
        configs=(),
        field_bindings=(),
        keypairs=keypairs,
        process_ownership=(),
    )


@pytest.mark.parametrize(
    "logical_id",
    [".", "..", "/absolute", "nested/resource", "nested\\resource", "bad\0id", "bad\nid"],
)
def test_resource_logical_ids_are_safe_filesystem_components(logical_id: str) -> None:
    with pytest.raises(ValidationError):
        _resource(logical_id, "/source/resource")


@pytest.mark.parametrize(
    "logical_id",
    [".", "..", "/absolute", "nested/config", "nested\\config", "bad\0id", "bad\tid"],
)
def test_config_logical_ids_are_safe_filesystem_components(logical_id: str) -> None:
    with pytest.raises(ValidationError):
        SourceConfigV1(
            logical_id=logical_id,
            capture_root=Path("/source"),
            captured_path=Path("/source/config.yaml"),
            process_cwd=Path("/source/service"),
            sha256="a" * 64,
        )


@pytest.mark.parametrize("handle", ["bad\0handle", "bad\rhandle", "bad\x7fhandle"])
def test_key_handles_reject_control_characters(handle: str) -> None:
    with pytest.raises(ValidationError):
        _keypair(handle, "a" * 64)


@pytest.mark.parametrize("logical_id", ["gateway.schema-v1", "agent-config.v2"])
def test_filesystem_component_ids_preserve_dots_and_hyphens(logical_id: str) -> None:
    assert _resource(logical_id, "/source/resource").logical_id == logical_id
    assert (
        SourceConfigV1(
            logical_id=logical_id,
            capture_root=Path("/source"),
            captured_path=Path("/source/config.yaml"),
            process_cwd=Path("/source/service"),
            sha256="a" * 64,
        ).logical_id
        == logical_id
    )


def test_resource_and_config_capture_roots_are_explicit_boundaries() -> None:
    resource = SourceResourceV1(
        logical_id="database",
        kind=ResourceKind.FILE,
        capture_kind=CaptureKind.COPY,
        capture_root=Path("/source/data"),
        captured_path=Path("/source/data/economy.db"),
    )
    config = SourceConfigV1(
        logical_id="gateway",
        capture_root=Path("/source/configs"),
        captured_path=Path("/source/configs/gateway.yaml"),
        process_cwd=Path("/installed/services/gateway"),
        sha256="a" * 64,
    )

    assert resource.capture_root == Path("/source/data")
    assert config.capture_root == Path("/source/configs")


@pytest.mark.parametrize(
    ("capture_root", "captured_path"),
    [
        (Path("relative"), Path("/source/data/economy.db")),
        (Path("/source/data/../data"), Path("/source/data/economy.db")),
        (Path("/source/data"), Path("/source/elsewhere/economy.db")),
    ],
)
def test_resource_capture_path_cannot_escape_its_lexical_root(
    capture_root: Path,
    captured_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        SourceResourceV1(
            logical_id="database",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.COPY,
            capture_root=capture_root,
            captured_path=captured_path,
        )


@pytest.mark.parametrize(
    ("capture_root", "captured_path"),
    [
        (Path("relative"), Path("/repository/schema.sql")),
        (Path("/repository/docs/../docs"), Path("/repository/docs/schema.sql")),
        (Path("/repository/docs"), Path("/repository/web/index.html")),
    ],
)
def test_config_capture_path_cannot_escape_its_lexical_root(
    capture_root: Path,
    captured_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        SourceConfigV1(
            logical_id="gateway",
            capture_root=capture_root,
            captured_path=captured_path,
            process_cwd=Path("/installed/services/gateway"),
            sha256="a" * 64,
        )


def test_directory_capture_path_may_equal_its_capture_root() -> None:
    resource = SourceResourceV1(
        logical_id="assets",
        kind=ResourceKind.DIRECTORY,
        capture_kind=CaptureKind.COPY,
        capture_root=Path("/source/assets"),
        captured_path=Path("/source/assets"),
    )

    assert resource.captured_path == resource.capture_root


def test_registered_agent_id_cannot_map_to_distinct_fingerprints() -> None:
    first = _keypair("operator", "a" * 64, agent_id="shared-agent")
    second = _keypair("poster", "b" * 64, agent_id="shared-agent")

    with pytest.raises(ValidationError):
        _inventory(
            resources=(*_key_resources("operator"), *_key_resources("poster")),
            keypairs=(first, second),
        )


def test_registered_agent_aliases_with_one_fingerprint_are_valid() -> None:
    first = _keypair("operator", "a" * 64, agent_id="shared-agent")
    second = _keypair("poster", "a" * 64, agent_id="shared-agent")

    inventory = _inventory(
        resources=(*_key_resources("operator"), *_key_resources("poster")),
        keypairs=(first, second),
    )

    assert inventory.keypairs == (first, second)


def test_gateway_fingerprints_are_unique() -> None:
    first = _keypair("gateway-a", "a" * 64, role=KeypairRole.GATEWAY)
    second = _keypair("gateway-b", "a" * 64, role=KeypairRole.GATEWAY)

    with pytest.raises(ValidationError):
        _inventory(
            resources=(*_key_resources("gateway-a"), *_key_resources("gateway-b")),
            keypairs=(first, second),
        )


def test_gateway_and_economic_fingerprint_sets_are_disjoint() -> None:
    economic = _keypair("operator", "a" * 64)
    gateway = _keypair("gateway", "a" * 64, role=KeypairRole.GATEWAY)

    with pytest.raises(ValidationError):
        _inventory(
            resources=(*_key_resources("operator"), *_key_resources("gateway")),
            keypairs=(economic, gateway),
        )


def test_partial_inventory_accepts_zero_or_one_exact_runtime_lock() -> None:
    lock_path = Path("/source/run/maintenance.lock")
    without_lock = _inventory(maintenance_lock_path=lock_path)
    lock = _resource(
        "maintenance-lock",
        str(lock_path),
        capture_kind=CaptureKind.RUNTIME_LOCK,
    )
    with_lock = _inventory(resources=(lock,), maintenance_lock_path=lock_path)

    assert without_lock.resources == ()
    assert with_lock.resources == (lock,)


def test_runtime_lock_resource_must_match_inventory_lock_path() -> None:
    wrong_lock = _resource(
        "maintenance-lock",
        "/source/run/different.lock",
        capture_kind=CaptureKind.RUNTIME_LOCK,
    )

    with pytest.raises(ValidationError):
        _inventory(resources=(wrong_lock,))


def test_inventory_rejects_multiple_runtime_lock_resources() -> None:
    first = _resource(
        "maintenance-lock-a",
        "/source/run/maintenance.lock",
        capture_kind=CaptureKind.RUNTIME_LOCK,
    )
    second = _resource(
        "maintenance-lock-b",
        "/source/run/maintenance.lock",
        capture_kind=CaptureKind.RUNTIME_LOCK,
    )

    with pytest.raises(ValidationError):
        _inventory(resources=(first, second))


@pytest.mark.parametrize(
    ("kind", "capture_kind"),
    [
        (ResourceKind.FILE, CaptureKind.ABSENT_FILE),
        (ResourceKind.FILE, CaptureKind.RUNTIME_LOCK),
        (ResourceKind.DIRECTORY, CaptureKind.COPY),
    ],
)
def test_duplicate_paths_reject_contradictory_resource_shapes(
    kind: ResourceKind,
    capture_kind: CaptureKind,
) -> None:
    canonical = _resource("canonical", "/source/shared")
    contradictory = _resource(
        "contradictory",
        "/source/shared",
        kind=kind,
        capture_kind=capture_kind,
    )

    with pytest.raises(ValidationError):
        _inventory(resources=(canonical, contradictory))


def test_duplicate_paths_with_identical_resource_shape_remain_structurally_valid() -> None:
    first = _resource("first", "/source/shared")
    alias = _resource("alias", "/source/shared")

    assert _inventory(resources=(first, alias)).resources == (first, alias)
