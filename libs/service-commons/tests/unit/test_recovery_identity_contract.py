"""Acceptance tests for explicit recovery key identity facts."""

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
    SourceKeypairV1,
    SourceResourceV1,
    canonical_json_bytes,
)

pytestmark = pytest.mark.unit

_FINGERPRINT = "a" * 64


def _keypair(
    *,
    handle: str = "operator",
    role: KeypairRole = KeypairRole.ECONOMIC,
    fingerprint: str = _FINGERPRINT,
    requirement: RegistrationRequirement = RegistrationRequirement.REQUIRED,
    state: CapturedRegistrationState = CapturedRegistrationState.REGISTERED,
    agent_id: str | None = "agent-operator",
) -> SourceKeypairV1:
    return SourceKeypairV1(
        handle=handle,
        role=role,
        private_resource_id=f"{handle}-private",
        public_resource_id=f"{handle}-public",
        expected_public_fingerprint_sha256=fingerprint,
        registration_requirement=requirement,
        captured_registration_state=state,
        expected_registered_agent_id=agent_id,
    )


def _resources(*handles: str) -> tuple[SourceResourceV1, ...]:
    return tuple(
        resource
        for handle in handles
        for resource in (
            SourceResourceV1(
                logical_id=f"{handle}-private",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.KEYPAIR,
                capture_root=Path("/source/keys"),
                captured_path=Path(f"/source/keys/{handle}.key"),
            ),
            SourceResourceV1(
                logical_id=f"{handle}-public",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.KEYPAIR,
                capture_root=Path("/source/keys"),
                captured_path=Path(f"/source/keys/{handle}.pub"),
            ),
        )
    )


def _inventory(*keypairs: SourceKeypairV1) -> RecoveryInventoryV1:
    return RecoveryInventoryV1(
        contract_version=1,
        runtime_id="runtime-1",
        source_root=Path("/source"),
        agents_package_root=Path("/source/agents/src"),
        maintenance_lock_path=Path("/source/run/maintenance.lock"),
        maintenance_lock_identity=MaintenanceLockIdentityV1(device=1, inode=2),
        resources=_resources(*(keypair.handle for keypair in keypairs)),
        configs=(),
        field_bindings=(),
        keypairs=keypairs,
        process_ownership=(),
    )


def test_registered_economic_identity_facts_round_trip_canonically() -> None:
    inventory = _inventory(_keypair())

    encoded = canonical_json_bytes(inventory)
    restored = RecoveryInventoryV1.model_validate_json(encoded)

    assert restored == inventory
    assert restored.keypairs[0].expected_public_fingerprint_sha256 == _FINGERPRINT
    assert restored.keypairs[0].expected_registered_agent_id == "agent-operator"


def test_optional_unregistered_economic_key_is_structurally_valid() -> None:
    keypair = _keypair(
        requirement=RegistrationRequirement.OPTIONAL,
        state=CapturedRegistrationState.UNREGISTERED,
        agent_id=None,
    )

    assert _inventory(keypair).keypairs == (keypair,)


def test_gateway_key_must_be_forbidden_from_economic_registration() -> None:
    gateway = _keypair(
        handle="gateway",
        role=KeypairRole.GATEWAY,
        requirement=RegistrationRequirement.FORBIDDEN,
        state=CapturedRegistrationState.UNREGISTERED,
        agent_id=None,
    )

    assert _inventory(gateway).keypairs == (gateway,)


@pytest.mark.parametrize(
    "fingerprint",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, "", " a" * 32],
)
def test_public_fingerprint_is_exact_lowercase_sha256(fingerprint: str) -> None:
    with pytest.raises(ValidationError):
        _keypair(fingerprint=fingerprint)


@pytest.mark.parametrize(
    ("requirement", "state", "agent_id"),
    [
        (
            RegistrationRequirement.REQUIRED,
            CapturedRegistrationState.UNREGISTERED,
            None,
        ),
        (
            RegistrationRequirement.OPTIONAL,
            CapturedRegistrationState.REGISTERED,
            None,
        ),
        (
            RegistrationRequirement.OPTIONAL,
            CapturedRegistrationState.UNREGISTERED,
            "agent-operator",
        ),
        (
            RegistrationRequirement.OPTIONAL,
            CapturedRegistrationState.REGISTERED,
            "",
        ),
        (
            RegistrationRequirement.OPTIONAL,
            CapturedRegistrationState.REGISTERED,
            " agent-operator",
        ),
    ],
)
def test_registration_state_and_expected_agent_id_must_agree(
    requirement: RegistrationRequirement,
    state: CapturedRegistrationState,
    agent_id: str | None,
) -> None:
    with pytest.raises(ValidationError):
        _keypair(requirement=requirement, state=state, agent_id=agent_id)


@pytest.mark.parametrize(
    ("role", "requirement", "state", "agent_id"),
    [
        (
            KeypairRole.ECONOMIC,
            RegistrationRequirement.FORBIDDEN,
            CapturedRegistrationState.UNREGISTERED,
            None,
        ),
        (
            KeypairRole.GATEWAY,
            RegistrationRequirement.REQUIRED,
            CapturedRegistrationState.REGISTERED,
            "gateway-agent",
        ),
        (
            KeypairRole.GATEWAY,
            RegistrationRequirement.OPTIONAL,
            CapturedRegistrationState.UNREGISTERED,
            None,
        ),
        (
            KeypairRole.GATEWAY,
            RegistrationRequirement.FORBIDDEN,
            CapturedRegistrationState.REGISTERED,
            "gateway-agent",
        ),
    ],
)
def test_registration_policy_is_constrained_by_key_role(
    role: KeypairRole,
    requirement: RegistrationRequirement,
    state: CapturedRegistrationState,
    agent_id: str | None,
) -> None:
    with pytest.raises(ValidationError):
        _keypair(role=role, requirement=requirement, state=state, agent_id=agent_id)


def test_economic_aliases_with_one_fingerprint_must_agree_on_identity() -> None:
    canonical = _keypair(handle="operator")
    contradictory_state = _keypair(
        handle="task-feeder",
        requirement=RegistrationRequirement.OPTIONAL,
        state=CapturedRegistrationState.UNREGISTERED,
        agent_id=None,
    )
    contradictory_agent = _keypair(handle="poster", agent_id="different-agent")

    with pytest.raises(ValidationError):
        _inventory(canonical, contradictory_state)
    with pytest.raises(ValidationError):
        _inventory(canonical, contradictory_agent)


def test_consistent_economic_aliases_with_one_fingerprint_are_valid() -> None:
    canonical = _keypair(handle="operator")
    alias = _keypair(handle="poster")

    assert _inventory(canonical, alias).keypairs == (canonical, alias)


def test_distinct_economic_fingerprints_may_have_distinct_identities() -> None:
    first = _keypair(handle="operator")
    second = _keypair(
        handle="worker",
        fingerprint="b" * 64,
        agent_id="agent-worker",
    )

    assert _inventory(first, second).keypairs == (first, second)


def test_all_identity_fields_are_required() -> None:
    required = set(SourceKeypairV1.model_json_schema()["required"])

    assert required == set(SourceKeypairV1.model_fields)


@pytest.mark.parametrize(
    "capture_kind",
    [CaptureKind.ABSENT_FILE, CaptureKind.RUNTIME_LOCK],
)
def test_non_payload_file_capture_intents_are_explicit(
    capture_kind: CaptureKind,
) -> None:
    resource = SourceResourceV1(
        logical_id="non-payload-file",
        kind=ResourceKind.FILE,
        capture_kind=capture_kind,
        capture_root=Path("/source"),
        captured_path=Path("/source/non-payload-file"),
    )

    assert resource.capture_kind is capture_kind


@pytest.mark.parametrize(
    "capture_kind",
    [CaptureKind.ABSENT_FILE, CaptureKind.RUNTIME_LOCK],
)
def test_non_payload_file_capture_intents_reject_directories(
    capture_kind: CaptureKind,
) -> None:
    with pytest.raises(ValidationError):
        SourceResourceV1(
            logical_id="non-payload-directory",
            kind=ResourceKind.DIRECTORY,
            capture_kind=capture_kind,
            capture_root=Path("/source"),
            captured_path=Path("/source/non-payload-directory"),
        )


@pytest.mark.parametrize(
    ("device", "inode"),
    [(-1, 1), (0, 0), (0, -1), (True, 1), (0, True), (0.0, 1), (0, 1.0)],
)
def test_maintenance_lock_identity_requires_strict_kernel_identifiers(
    device: object,
    inode: object,
) -> None:
    with pytest.raises(ValidationError):
        MaintenanceLockIdentityV1(device=device, inode=inode)


def test_maintenance_lock_identity_is_required_immutable_source_evidence() -> None:
    identity = MaintenanceLockIdentityV1(device=0, inode=1)

    assert set(MaintenanceLockIdentityV1.model_json_schema()["required"]) == {
        "device",
        "inode",
    }
    with pytest.raises(ValidationError):
        identity.inode = 2

    payload = _inventory().model_dump()
    del payload["maintenance_lock_identity"]
    with pytest.raises(ValidationError):
        RecoveryInventoryV1.model_validate(payload)
