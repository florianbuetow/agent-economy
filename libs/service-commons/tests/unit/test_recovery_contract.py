"""Unit tests for the neutral recovery inventory contract."""

import operator
from pathlib import Path

import pytest
from pydantic import ValidationError

from service_commons.recovery_contract import (
    BindingKind,
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
    MaintenanceLockIdentityV1,
    OwnedProcessV1,
    PathResolutionBase,
    RecoveryInventoryV1,
    RegistrationRequirement,
    ResourceKind,
    SourceConfigV1,
    SourceFieldBindingV1,
    SourceKeypairV1,
    SourceResourceV1,
    TargetBindingsV1,
    canonical_json_bytes,
    canonical_sha256,
)

pytestmark = pytest.mark.unit

_DIGEST = "a" * 64


def _config(*, logical_id: str = "gateway") -> SourceConfigV1:
    return SourceConfigV1(
        logical_id=logical_id,
        capture_root=Path("/source/configs"),
        captured_path=Path(f"/source/configs/{logical_id}.yaml"),
        process_cwd=Path(f"/source/services/{logical_id}"),
        sha256=_DIGEST,
    )


def _resource(*, logical_id: str = "database") -> SourceResourceV1:
    return SourceResourceV1(
        logical_id=logical_id,
        kind=ResourceKind.FILE,
        capture_kind=CaptureKind.COPY,
        capture_root=Path("/source/services/gateway/data"),
        captured_path=Path("/source/services/gateway/data/economy.db"),
    )


def _filesystem_binding(
    *,
    binding_id: str = "gateway.database.path",
    config_id: str = "gateway",
    field_path: tuple[str | int, ...] = ("database", "path"),
    captured_value: str = "data/economy.db",
    resolution_base: PathResolutionBase = PathResolutionBase.PROCESS_CWD,
    resolved_source_path: Path = Path("/source/services/gateway/data/economy.db"),
    resource_id: str = "database",
) -> SourceFieldBindingV1:
    return SourceFieldBindingV1(
        binding_id=binding_id,
        config_id=config_id,
        field_path=field_path,
        binding_kind=BindingKind.FILESYSTEM,
        captured_value=captured_value,
        resolution_base=resolution_base,
        resolved_source_path=resolved_source_path,
        resource_id=resource_id,
    )


def _key_resources(handle: str = "operator") -> tuple[SourceResourceV1, SourceResourceV1]:
    return (
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


def _keypair(handle: str = "operator") -> SourceKeypairV1:
    return SourceKeypairV1(
        handle=handle,
        role=KeypairRole.ECONOMIC,
        private_resource_id=f"{handle}-private",
        public_resource_id=f"{handle}-public",
        expected_public_fingerprint_sha256="a" * 64,
        registration_requirement=RegistrationRequirement.REQUIRED,
        captured_registration_state=CapturedRegistrationState.REGISTERED,
        expected_registered_agent_id="agent-operator",
    )


def _inventory(
    *,
    resources: tuple[SourceResourceV1, ...] | None = None,
    configs: tuple[SourceConfigV1, ...] | None = None,
    field_bindings: tuple[SourceFieldBindingV1, ...] | None = None,
    keypairs: tuple[SourceKeypairV1, ...] | None = None,
) -> RecoveryInventoryV1:
    return RecoveryInventoryV1(
        contract_version=1,
        runtime_id="runtime-1",
        source_root=Path("/source"),
        agents_package_root=Path("/source/agents/src"),
        maintenance_lock_path=Path("/source/run/maintenance.lock"),
        maintenance_lock_identity=MaintenanceLockIdentityV1(device=1, inode=2),
        resources=resources if resources is not None else (_resource(),),
        configs=configs if configs is not None else (_config(),),
        field_bindings=(field_bindings if field_bindings is not None else (_filesystem_binding(),)),
        keypairs=keypairs if keypairs is not None else (),
        process_ownership=(),
    )


def test_valid_inventory_round_trips_through_canonical_json() -> None:
    inventory = _inventory()

    encoded = canonical_json_bytes(inventory)

    assert RecoveryInventoryV1.model_validate_json(encoded) == inventory
    assert encoded == canonical_json_bytes(inventory)
    assert canonical_sha256(inventory) == canonical_sha256(
        RecoveryInventoryV1.model_validate_json(encoded)
    )


def test_canonical_json_ignores_mapping_insertion_order() -> None:
    first = TargetBindingsV1(
        contract_version=1,
        target_root=Path("/target"),
        process_cwds={"gateway": Path("/installed/gateway"), "ui": Path("/installed/ui")},
        agents_package_root=Path("/installed/agents/src"),
        resource_paths={"database": Path("/target/db.sqlite"), "assets": Path("/target/assets")},
        endpoint_values={"gateway.port": 8123, "ui.origins": ("http://127.0.0.1:8124",)},
    )
    second = TargetBindingsV1(
        contract_version=1,
        target_root=Path("/target"),
        process_cwds={"ui": Path("/installed/ui"), "gateway": Path("/installed/gateway")},
        agents_package_root=Path("/installed/agents/src"),
        resource_paths={"assets": Path("/target/assets"), "database": Path("/target/db.sqlite")},
        endpoint_values={"ui.origins": ("http://127.0.0.1:8124",), "gateway.port": 8123},
    )

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_sha256(first) == canonical_sha256(second)


@pytest.mark.parametrize("model", [RecoveryInventoryV1, TargetBindingsV1])
def test_unknown_contract_version_is_rejected(model: type[object]) -> None:
    payload = (
        _inventory().model_dump()
        if model is RecoveryInventoryV1
        else TargetBindingsV1(
            contract_version=1,
            target_root=Path("/target"),
            process_cwds={},
            agents_package_root=Path("/installed/agents/src"),
            resource_paths={},
            endpoint_values={},
        ).model_dump()
    )
    payload["contract_version"] = 2

    with pytest.raises(ValidationError):
        model.model_validate(payload)  # type: ignore[attr-defined]


@pytest.mark.parametrize("model", [RecoveryInventoryV1, TargetBindingsV1])
@pytest.mark.parametrize("invalid_version", [True, 1.0])
def test_contract_version_rejects_values_that_only_compare_equal_to_one(
    model: type[object], invalid_version: object
) -> None:
    payload = (
        _inventory().model_dump()
        if model is RecoveryInventoryV1
        else TargetBindingsV1(
            contract_version=1,
            target_root=Path("/target"),
            process_cwds={},
            agents_package_root=Path("/installed/agents/src"),
            resource_paths={},
            endpoint_values={},
        ).model_dump()
    )
    payload["contract_version"] = invalid_version

    with pytest.raises(ValidationError):
        model.model_validate(payload)  # type: ignore[attr-defined]


def test_unknown_fields_and_binding_kinds_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SourceResourceV1.model_validate(
            {
                "logical_id": "database",
                "kind": "file",
                "capture_kind": "copy",
                "capture_root": Path("/source"),
                "captured_path": Path("/source/db"),
                "unexpected": "value",
            }
        )

    payload = _filesystem_binding().model_dump()
    payload["binding_kind"] = "url_or_maybe_path"
    with pytest.raises(ValidationError):
        SourceFieldBindingV1.model_validate(payload)


@pytest.mark.parametrize(
    ("resource_kind", "capture_kind"),
    [
        (ResourceKind.FILE, CaptureKind.DIRECTORY_ONLY),
        (ResourceKind.DIRECTORY, CaptureKind.KEYPAIR),
        (ResourceKind.DIRECTORY, CaptureKind.CONFIG),
    ],
)
def test_resource_capture_intent_must_match_filesystem_shape(
    resource_kind: ResourceKind, capture_kind: CaptureKind
) -> None:
    with pytest.raises(ValidationError):
        SourceResourceV1(
            logical_id="invalid-resource",
            kind=resource_kind,
            capture_kind=capture_kind,
            capture_root=Path("/source"),
            captured_path=Path("/source/resource"),
        )


def test_resource_capture_intents_cover_copy_key_config_and_directory_only() -> None:
    resources = (
        SourceResourceV1(
            logical_id="assets",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.COPY,
            capture_root=Path("/source/assets"),
            captured_path=Path("/source/assets"),
        ),
        SourceResourceV1(
            logical_id="key-root",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.DIRECTORY_ONLY,
            capture_root=Path("/source/keys"),
            captured_path=Path("/source/keys"),
        ),
        SourceResourceV1(
            logical_id="agent-config",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            capture_root=Path("/source/agents"),
            captured_path=Path("/source/agents/config.yaml"),
        ),
        SourceResourceV1(
            logical_id="optional-input",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.ABSENT_FILE,
            capture_root=Path("/source/agents"),
            captured_path=Path("/source/agents/optional-input.jsonl"),
        ),
        SourceResourceV1(
            logical_id="maintenance-lock",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.RUNTIME_LOCK,
            capture_root=Path("/source/run"),
            captured_path=Path("/source/run/maintenance.lock"),
        ),
        *_key_resources(),
    )

    assert {resource.capture_kind for resource in resources} == set(CaptureKind)


def test_valid_keypair_inventory_references_canonical_pair_resources() -> None:
    resources = _key_resources()
    inventory = _inventory(
        resources=resources,
        configs=(),
        field_bindings=(),
        keypairs=(_keypair(),),
    )

    assert inventory.keypairs == (_keypair(),)


@pytest.mark.parametrize(
    ("resources", "keypairs"),
    [
        (
            _key_resources(),
            (
                SourceKeypairV1(
                    handle="wrong-handle",
                    role=KeypairRole.ECONOMIC,
                    private_resource_id="operator-private",
                    public_resource_id="operator-public",
                    expected_public_fingerprint_sha256="a" * 64,
                    registration_requirement=RegistrationRequirement.REQUIRED,
                    captured_registration_state=CapturedRegistrationState.REGISTERED,
                    expected_registered_agent_id="agent-operator",
                ),
            ),
        ),
        (
            (
                SourceResourceV1(
                    logical_id="operator-private",
                    kind=ResourceKind.FILE,
                    capture_kind=CaptureKind.COPY,
                    capture_root=Path("/source/keys"),
                    captured_path=Path("/source/keys/operator.key"),
                ),
                _key_resources()[1],
            ),
            (_keypair(),),
        ),
        (
            _key_resources(),
            (
                SourceKeypairV1(
                    handle="operator",
                    role=KeypairRole.GATEWAY,
                    private_resource_id="missing-private",
                    public_resource_id="operator-public",
                    expected_public_fingerprint_sha256="a" * 64,
                    registration_requirement=RegistrationRequirement.FORBIDDEN,
                    captured_registration_state=CapturedRegistrationState.UNREGISTERED,
                    expected_registered_agent_id=None,
                ),
            ),
        ),
    ],
)
def test_keypair_inventory_rejects_bad_names_capture_kinds_and_references(
    resources: tuple[SourceResourceV1, ...], keypairs: tuple[SourceKeypairV1, ...]
) -> None:
    with pytest.raises(ValidationError):
        _inventory(resources=resources, configs=(), field_bindings=(), keypairs=keypairs)


def test_keypair_inventory_rejects_duplicate_handles_or_reused_resources() -> None:
    resources = _key_resources()
    duplicate_handle = SourceKeypairV1(
        handle="operator",
        role=KeypairRole.GATEWAY,
        private_resource_id="operator-private",
        public_resource_id="operator-public",
        expected_public_fingerprint_sha256="a" * 64,
        registration_requirement=RegistrationRequirement.FORBIDDEN,
        captured_registration_state=CapturedRegistrationState.UNREGISTERED,
        expected_registered_agent_id=None,
    )

    with pytest.raises(ValidationError):
        _inventory(
            resources=resources,
            configs=(),
            field_bindings=(),
            keypairs=(_keypair(), duplicate_handle),
        )

    reused_resources = SourceKeypairV1(
        handle="different-handle",
        role=KeypairRole.GATEWAY,
        private_resource_id="operator-private",
        public_resource_id="operator-public",
        expected_public_fingerprint_sha256="b" * 64,
        registration_requirement=RegistrationRequirement.FORBIDDEN,
        captured_registration_state=CapturedRegistrationState.UNREGISTERED,
        expected_registered_agent_id=None,
    )
    with pytest.raises(ValidationError):
        _inventory(
            resources=resources,
            configs=(),
            field_bindings=(),
            keypairs=(_keypair(), reused_resources),
        )


@pytest.mark.parametrize("captured_value", [True, 8123.0, "8123"])
def test_port_bindings_reject_non_strict_integers(captured_value: object) -> None:
    with pytest.raises(ValidationError):
        SourceFieldBindingV1(
            binding_id="gateway.server.port",
            config_id="gateway",
            field_path=("server", "port"),
            binding_kind=BindingKind.PORT,
            captured_value=captured_value,
            resolution_base=None,
            resolved_source_path=None,
            resource_id=None,
        )


def test_binding_kinds_require_their_exact_value_and_path_shape() -> None:
    with pytest.raises(ValidationError):
        SourceFieldBindingV1(
            binding_id="gateway.url",
            config_id="gateway",
            field_path=("db_gateway", "url"),
            binding_kind=BindingKind.NETWORK,
            captured_value="http://127.0.0.1:8007",
            resolution_base=PathResolutionBase.PROCESS_CWD,
            resolved_source_path=Path("/source/not-a-url"),
            resource_id="database",
        )

    with pytest.raises(ValidationError):
        SourceFieldBindingV1(
            binding_id="ui.origins",
            config_id="ui",
            field_path=("browser_security", "allowed_origins"),
            binding_kind=BindingKind.ORIGINS,
            captured_value="http://127.0.0.1:8008",
            resolution_base=None,
            resolved_source_path=None,
            resource_id=None,
        )


@pytest.mark.parametrize(
    ("field_path", "captured_value"),
    [
        (("workers", "*", "llm", "base_url"), "http://provider.invalid"),
        (("workers.*.llm.base_url",), "http://provider.invalid"),
        (("workers", -1, "llm", "base_url"), "http://provider.invalid"),
        ((), "http://provider.invalid"),
    ],
)
def test_field_paths_must_be_concrete_segments(
    field_path: tuple[str | int, ...], captured_value: str
) -> None:
    with pytest.raises(ValidationError):
        SourceFieldBindingV1(
            binding_id="agents.worker.endpoint",
            config_id="agents",
            field_path=field_path,
            binding_kind=BindingKind.NETWORK,
            captured_value=captured_value,
            resolution_base=None,
            resolved_source_path=None,
            resource_id=None,
        )


def test_inventory_rejects_duplicate_ids_and_config_field_pairs() -> None:
    with pytest.raises(ValidationError):
        _inventory(resources=(_resource(), _resource()))

    duplicate_field = _filesystem_binding(binding_id="different-binding-id")
    with pytest.raises(ValidationError):
        _inventory(field_bindings=(_filesystem_binding(), duplicate_field))

    with pytest.raises(ValidationError):
        _inventory(configs=(_config(), _config()))


def test_inventory_rejects_duplicate_or_inconsistent_process_ownership() -> None:
    process = OwnedProcessV1(
        logical_id="gateway-process",
        config_id="gateway",
        process_cwd=Path("/source/services/gateway"),
        agents_package_root=None,
        pid=101,
        pgid=101,
        sid=99,
        running=True,
        group_drained=False,
        leader_returncode=None,
        cleanup_error=None,
    )
    payload = _inventory().model_dump()
    payload["process_ownership"] = (process, process)
    with pytest.raises(ValidationError):
        RecoveryInventoryV1.model_validate(payload)

    wrong_cwd = OwnedProcessV1(
        logical_id="gateway-process",
        config_id="gateway",
        process_cwd=Path("/source/wrong-cwd"),
        agents_package_root=None,
        pid=None,
        pgid=None,
        sid=None,
        running=False,
        group_drained=False,
        leader_returncode=None,
        cleanup_error=None,
    )
    payload["process_ownership"] = (wrong_cwd,)
    with pytest.raises(ValidationError):
        RecoveryInventoryV1.model_validate(payload)

    missing_config = OwnedProcessV1(
        logical_id="unknown-process",
        config_id="missing",
        process_cwd=Path("/source/services/missing"),
        agents_package_root=None,
        pid=None,
        pgid=None,
        sid=None,
        running=False,
        group_drained=False,
        leader_returncode=None,
        cleanup_error=None,
    )
    payload["process_ownership"] = (missing_config,)
    with pytest.raises(ValidationError):
        RecoveryInventoryV1.model_validate(payload)


@pytest.mark.parametrize(
    ("pid", "pgid", "sid"),
    [(1, None, 1), (0, 1, 1), (-1, 1, 1), (True, 1, 1)],
)
def test_process_identity_fields_are_strict_positive_and_all_or_none(
    pid: object, pgid: object, sid: object
) -> None:
    with pytest.raises(ValidationError):
        OwnedProcessV1(
            logical_id="process",
            config_id=None,
            process_cwd=Path("/source/service"),
            agents_package_root=None,
            pid=pid,
            pgid=pgid,
            sid=sid,
            running=False,
            group_drained=False,
            leader_returncode=None,
            cleanup_error=None,
        )


@pytest.mark.parametrize(
    ("running", "group_drained", "leader_returncode"),
    [(True, True, None), (True, False, 0), (False, True, None)],
)
def test_process_state_rejects_inconsistent_runtime_facts(
    running: bool, group_drained: bool, leader_returncode: int | None
) -> None:
    ids = (None, None, None) if group_drained else (101, 101, 99)
    with pytest.raises(ValidationError):
        OwnedProcessV1(
            logical_id="process",
            config_id=None,
            process_cwd=Path("/source/service"),
            agents_package_root=None,
            pid=ids[0],
            pgid=ids[1],
            sid=ids[2],
            running=running,
            group_drained=group_drained,
            leader_returncode=leader_returncode,
            cleanup_error=None,
        )


def test_stopped_process_record_preserves_observed_cleanup_facts() -> None:
    process = OwnedProcessV1(
        logical_id="gateway-process",
        config_id=None,
        process_cwd=Path("/source/services/gateway"),
        agents_package_root=None,
        pid=101,
        pgid=101,
        sid=99,
        running=False,
        group_drained=True,
        leader_returncode=-15,
        cleanup_error="forced termination was required",
    )

    assert process.group_drained is True
    assert process.cleanup_error == "forced termination was required"


def test_inventory_enforces_config_and_resource_references() -> None:
    with pytest.raises(ValidationError):
        _inventory(field_bindings=(_filesystem_binding(config_id="missing"),))

    with pytest.raises(ValidationError):
        _inventory(field_bindings=(_filesystem_binding(resource_id="missing"),))


@pytest.mark.parametrize(
    ("binding", "configs"),
    [
        (
            _filesystem_binding(resolved_source_path=Path("/wrong/economy.db")),
            (_config(),),
        ),
        (
            _filesystem_binding(
                captured_value="/source/services/gateway/data/economy.db",
                resolution_base=PathResolutionBase.PROCESS_CWD,
            ),
            (_config(),),
        ),
        (
            _filesystem_binding(
                captured_value="../secrets/key.pub",
                resolution_base=PathResolutionBase.CONFIG_PARENT,
                resolved_source_path=Path("/source/wrong/key.pub"),
            ),
            (_config(),),
        ),
    ],
)
def test_inventory_rejects_inconsistent_lexical_path_resolution(
    binding: SourceFieldBindingV1, configs: tuple[SourceConfigV1, ...]
) -> None:
    with pytest.raises(ValidationError):
        _inventory(configs=configs, field_bindings=(binding,))


def test_inventory_uses_captured_config_parent_and_agents_anchor() -> None:
    config_binding = _filesystem_binding(
        binding_id="gateway.auth.key",
        field_path=("auth", "trusted_callers", "identity", "public_key_path"),
        captured_value="../keys/identity.pub",
        resolution_base=PathResolutionBase.CONFIG_PARENT,
        resolved_source_path=Path("/source/keys/identity.pub"),
        resource_id="identity-key",
    )
    agents_binding = _filesystem_binding(
        binding_id="agents.tasks.file",
        config_id="agents",
        field_path=("task_feeder", "tasks_file"),
        captured_value="task_feeder/tasks.yaml",
        resolution_base=PathResolutionBase.AGENTS_PACKAGE_ROOT,
        resolved_source_path=Path("/source/agents/src/task_feeder/tasks.yaml"),
        resource_id="tasks-file",
    )
    inventory = _inventory(
        resources=(
            SourceResourceV1(
                logical_id="identity-key",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.KEYPAIR,
                capture_root=Path("/source/keys"),
                captured_path=Path("/source/keys/identity.pub"),
            ),
            SourceResourceV1(
                logical_id="tasks-file",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.COPY,
                capture_root=Path("/source/agents/src/task_feeder"),
                captured_path=Path("/source/agents/src/task_feeder/tasks.yaml"),
            ),
        ),
        configs=(_config(), _config(logical_id="agents")),
        field_bindings=(config_binding, agents_binding),
    )

    assert len(inventory.field_bindings) == 2


def test_partial_runtime_inventory_is_structurally_valid() -> None:
    inventory = RecoveryInventoryV1(
        contract_version=1,
        runtime_id="partial-runtime",
        source_root=Path("/source"),
        agents_package_root=Path("/source/agents/src"),
        maintenance_lock_path=Path("/source/run/maintenance.lock"),
        maintenance_lock_identity=MaintenanceLockIdentityV1(device=1, inode=2),
        resources=(),
        configs=(),
        field_bindings=(),
        keypairs=(),
        process_ownership=(),
    )

    assert inventory.resources == ()


def test_target_resource_paths_must_remain_beneath_target_root() -> None:
    with pytest.raises(ValidationError):
        TargetBindingsV1(
            contract_version=1,
            target_root=Path("/target"),
            process_cwds={"gateway": Path("/installed/gateway")},
            agents_package_root=Path("/installed/agents/src"),
            resource_paths={"database": Path("/outside/db.sqlite")},
            endpoint_values={},
        )


def test_target_code_anchors_may_live_outside_target_root() -> None:
    target = TargetBindingsV1(
        contract_version=1,
        target_root=Path("/target"),
        process_cwds={"gateway": Path("/installed/gateway")},
        agents_package_root=Path("/installed/agents/src"),
        resource_paths={"database": Path("/target/database/economy.db")},
        endpoint_values={"gateway.port": 8123},
    )

    assert target.process_cwds["gateway"] == Path("/installed/gateway")


@pytest.mark.parametrize(
    ("field_name", "key", "value"),
    [
        ("process_cwds", "escape", Path("/outside/process")),
        ("resource_paths", "escape", Path("/outside/resource")),
        ("endpoint_values", "escape", "http://outside.invalid"),
    ],
)
def test_target_mapping_fields_are_immutable_and_hash_stable(
    field_name: str, key: str, value: object
) -> None:
    target = TargetBindingsV1(
        contract_version=1,
        target_root=Path("/target"),
        process_cwds={"gateway": Path("/installed/gateway")},
        agents_package_root=Path("/installed/agents/src"),
        resource_paths={"database": Path("/target/database/economy.db")},
        endpoint_values={"gateway.port": 8123},
    )
    canonical_before = canonical_json_bytes(target)
    hash_before = canonical_sha256(target)

    with pytest.raises(TypeError):
        operator.setitem(getattr(target, field_name), key, value)

    assert canonical_json_bytes(target) == canonical_before
    assert canonical_sha256(target) == hash_before


def test_target_mapping_fields_are_detached_from_input_aliases() -> None:
    process_cwds = {"gateway": Path("/installed/gateway")}
    resource_paths = {"database": Path("/target/database/economy.db")}
    endpoint_values: dict[str, str | int | tuple[str, ...]] = {"gateway.port": 8123}
    target = TargetBindingsV1(
        contract_version=1,
        target_root=Path("/target"),
        process_cwds=process_cwds,
        agents_package_root=Path("/installed/agents/src"),
        resource_paths=resource_paths,
        endpoint_values=endpoint_values,
    )
    canonical_before = canonical_json_bytes(target)
    hash_before = canonical_sha256(target)

    process_cwds["gateway"] = Path("/outside/process")
    resource_paths["database"] = Path("/outside/resource")
    endpoint_values["gateway.port"] = 9999

    assert target.process_cwds["gateway"] == Path("/installed/gateway")
    assert target.resource_paths["database"] == Path("/target/database/economy.db")
    assert target.endpoint_values["gateway.port"] == 8123
    assert canonical_json_bytes(target) == canonical_before
    assert canonical_sha256(target) == hash_before


def test_contract_models_have_no_configurable_defaults_and_are_frozen() -> None:
    required = set(SourceFieldBindingV1.model_json_schema()["required"])
    assert required == set(SourceFieldBindingV1.model_fields)

    resource = _resource()
    with pytest.raises(ValidationError):
        resource.logical_id = "replacement"
