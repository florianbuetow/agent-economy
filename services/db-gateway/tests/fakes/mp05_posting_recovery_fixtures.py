from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from service_auth import generate_keypair
from service_auth.financial_identity import operation_id_for
from service_auth.posting_journal import (
    canonical_json_bytes,
    canonical_sha256,
    new_head,
    ordered_entries,
    parse_source_bytes,
)
from service_auth.signing import keypair_fingerprint, load_public_key, public_key_to_b64
from service_auth.task_postings import TaskPostingIntent
from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock
from service_commons.recovery_contract import (
    BindingKind,
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
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
)

from tests.unit.test_mp06_recovery import _bindings as base_bindings
from tests.unit.test_mp06_recovery import _source

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

URLS = {
    "identity_url": "https://identity.recovery.example/v1",
    "bank_url": "https://bank.recovery.example/v1",
    "task_board_url": "https://board.recovery.example/v1",
    "reputation_url": "https://reputation.recovery.example/v1",
    "court_url": "https://court.recovery.example/v1",
}
FEEDER_HANDLE = "feeder-recovery"
FEEDER_AGENT_ID = "a-feeder-recovery"
SOURCE_BYTES = (
    b'{"level":2,"problem_type":"proof","solution_note":null,'
    b'"solutions":["42"],"spec":"Preserve this exact source entry.",'
    b'"title":"Recovery audit task"}\n'
)
MAX_SOURCE_BYTES = 65_536
MAX_RECORD_BYTES = 65_536
DIAGNOSTIC_LIMIT = 8


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write(path: Path, value: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value)
    path.chmod(mode)


def canonical_public_key(path: Path) -> str:
    return f"ed25519:{public_key_to_b64(load_public_key(path))}"


def tree_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def tree_modes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat(follow_symlinks=False).st_mode & 0o777
        for path in root.rglob("*")
        if path.is_file()
    }


def _owner(fingerprint: str, **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "environment_id": "mp05-recovery-tests",
        "role": "task-feeder",
        "profile": FEEDER_HANDLE,
        "handle": FEEDER_HANDLE,
        "registered_agent_id": FEEDER_AGENT_ID,
        "key_fingerprint": fingerprint,
        "service_urls": dict(URLS),
    }
    value.update(updates)
    return value


def _source_binding(tasks: Path) -> dict[str, object]:
    return {
        "source_path": str(tasks.resolve()),
        "source_snapshot_sha256": digest(SOURCE_BYTES),
        "source_size_bytes": len(SOURCE_BYTES),
        "source_entry_count": 1,
        "posting_terms": {
            "base_reward": 7,
            "reward_per_level": 3,
            "bidding_deadline_seconds": 60,
            "acceptance_window_seconds": 30,
            "execution_deadline_seconds": 120,
            "review_deadline_seconds": 90,
            "shuffle": False,
        },
    }


def _prepared_record(
    owner: dict[str, object], tasks: Path
) -> tuple[dict[str, object], dict[str, object]]:
    binding = _source_binding(tasks)
    head = new_head(canonical_sha256(owner), binding)
    snapshot = parse_source_bytes(SOURCE_BYTES, source_path=tasks, max_bytes=MAX_SOURCE_BYTES)
    entry = ordered_entries(snapshot, binding, cycle_index=0)[0]
    task_id = "t-00000000-0000-4000-8000-000000000502"
    operation_id = "op-00000000-0000-4000-8000-000000000501"
    intent = TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": FEEDER_AGENT_ID,
            "lock_operation_id": operation_id_for("task-lock", (task_id, FEEDER_AGENT_ID)),
            "title": "Recovery audit task",
            "spec": "Preserve this exact source entry.",
            "reward": 13,
            "bidding_deadline_seconds": 60,
            "acceptance_window_seconds": 30,
            "execution_deadline_seconds": 120,
            "review_deadline_seconds": 90,
        }
    )
    binding_sha256 = str(head["source_binding_sha256"])
    cycle_id = canonical_sha256({"source_binding_sha256": binding_sha256, "cycle_index": 0})
    selection_id = canonical_sha256(
        {"cycle_id": cycle_id, "cycle_position": 0, "source_entry_id": entry.entry_id}
    )
    semantic = {
        "schema_version": 1,
        "owner_sha256": head["owner_sha256"],
        "source_binding_sha256": binding_sha256,
        "selection_sequence": 0,
        "selection_id": selection_id,
        "cycle_index": 0,
        "cycle_id": cycle_id,
        "cycle_position": 0,
        "source_entry_index": entry.index,
        "source_line_number": entry.line_number,
        "source_line_sha256": entry.line_sha256,
        "source_entry_id": entry.entry_id,
        "raw_task_sha256": entry.raw_task_sha256,
        "raw_task": entry.raw_task.as_json(),
        "intent": intent.as_json(),
        "request_sha256": intent.request_sha256,
    }
    semantic_sha256 = canonical_sha256(semantic)
    selection_chain = canonical_sha256(
        {"previous_chain_sha256": head["chain_sha256"], "semantic_sha256": semantic_sha256}
    )
    record = {
        **semantic,
        "semantic_sha256": semantic_sha256,
        "previous_chain_sha256": head["chain_sha256"],
        "selection_chain_sha256": selection_chain,
        "revision": 0,
        "state": "prepared",
        "observed_status_code": None,
        "publication": None,
        "diagnostics": [],
    }
    return head, record


def _write_journal(identity: Path, owner: dict[str, object], tasks: Path, state: str) -> None:
    postings = identity / "postings"
    records = postings / "records"
    records.mkdir(parents=True, mode=0o700)
    postings.chmod(0o700)
    records.chmod(0o700)
    if state == "empty":
        return
    write(postings / "source.jsonl", SOURCE_BYTES, mode=0o600)
    if state == "unbound_source":
        return
    head, record = _prepared_record(owner, tasks)
    if state == "ready":
        head.update(
            {
                "revision": 1,
                "committed_selection_count": 1,
                "last_selection_id": record["selection_id"],
                "last_record_semantic_sha256": record["semantic_sha256"],
                "chain_sha256": record["selection_chain_sha256"],
            }
        )
    write(postings / "head.json", canonical_json_bytes(head), mode=0o600)
    write(records / "00000000000000000000.json", canonical_json_bytes(record), mode=0o600)


@dataclass(slots=True)
class Fixture:
    inventory: RecoveryInventoryV1
    connection: sqlite3.Connection
    runtime_root: Path
    identity: Path
    fingerprint: str
    tasks: Path

    def bindings(self, target: Path, *, relocated_tasks: bool = False) -> TargetBindingsV1:
        base = base_bindings(self.inventory, target)
        paths = dict(base.resource_paths)
        paths.update(
            {
                "config.agents": target / "configs/agents.yaml",
                "agents.runtime_state": target / "agent-state",
                "agents.tasks": (
                    target / "relocated/tasks.jsonl"
                    if relocated_tasks
                    else target / "external-feeds/tasks.jsonl"
                ),
                "keys.root": target / "keys",
                f"key.{FEEDER_HANDLE}.private": target / f"keys/{FEEDER_HANDLE}.key",
                f"key.{FEEDER_HANDLE}.public": target / f"keys/{FEEDER_HANDLE}.pub",
            }
        )
        endpoints = dict(base.endpoint_values)
        endpoints.update({f"agents:platform.{name}": value for name, value in URLS.items()})
        return TargetBindingsV1(
            contract_version=1,
            target_root=target,
            process_cwds={**base.process_cwds, "agents": target / "agents-runtime"},
            agents_package_root=target / "agents-package",
            resource_paths=paths,
            endpoint_values=endpoints,
        )


def make_fixture(
    tmp_path: Path,
    *,
    state: str = "ready",
    namespace: bool = True,
    omit_limit: str | None = None,
    roster_has_feeder: bool = True,
    owner_updates: dict[str, object] | None = None,
) -> Fixture:
    inventory, connection = _source(tmp_path)
    source = inventory.source_root
    keys = source / "keys"
    generate_keypair(FEEDER_HANDLE, keys)
    fingerprint = keypair_fingerprint(FEEDER_HANDLE, keys, allow_legacy_private_mode=False)
    tasks = source / "external-feeds/tasks.jsonl"
    write(tasks, SOURCE_BYTES, mode=0o600)
    roster = source / "agents/roster.yaml"
    roster_agents: dict[str, object] = {"operator": {"name": "Operator", "type": "operator"}}
    if roster_has_feeder:
        roster_agents[FEEDER_HANDLE] = {"name": "Recovery feeder", "type": "poster"}
    write(roster, canonical_json_bytes({"agents": roster_agents}), mode=0o600)
    runtime_root = source / "agent-state"
    runtime_root.mkdir(mode=0o700)
    identities = runtime_root / "identities"
    identities.mkdir(mode=0o700)
    identity = identities / fingerprint
    intents = identity / "intents"
    intents.mkdir(parents=True, mode=0o700)
    identity.chmod(0o700)
    owner = _owner(fingerprint, **(owner_updates or {}))
    write(identity / "owner.json", canonical_json_bytes(owner), mode=0o600)
    with hold_maintenance_lock(
        identity / "runtime.lock", mode=MaintenanceLockMode.EXCLUSIVE, blocking=False
    ):
        pass
    if namespace:
        _write_journal(identity, owner, tasks, state)

    config: dict[str, Any] = {
        "platform": dict(URLS),
        "data": {"keys_dir": "../keys", "roster_path": "../agents/roster.yaml"},
        "runtime_state": {
            "environment_id": "mp05-recovery-tests",
            "directory": "../agent-state",
            "diagnostic_limit": DIAGNOSTIC_LIMIT,
            "max_record_bytes": MAX_RECORD_BYTES,
        },
        "task_feeder": {
            "handle": FEEDER_HANDLE,
            "tasks_file": "../external-feeds/tasks.jsonl",
            "source_max_bytes": MAX_SOURCE_BYTES,
        },
    }
    if omit_limit == "source_max_bytes":
        del config["task_feeder"]["source_max_bytes"]
    elif omit_limit in {"max_record_bytes", "diagnostic_limit"}:
        del config["runtime_state"][omit_limit]
    agents_config = source / "configs/agents.yaml"
    config_bytes = canonical_json_bytes(config)
    write(agents_config, config_bytes, mode=0o600)

    connection.execute(
        "CREATE TABLE identity_agents "
        "(agent_id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key TEXT NOT NULL, "
        "registered_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO identity_agents VALUES (?, ?, ?, ?)",
        (
            FEEDER_AGENT_ID,
            "Untrusted display name",
            canonical_public_key(keys / f"{FEEDER_HANDLE}.pub"),
            "2026-10-19T00:00:00Z",
        ),
    )
    connection.commit()

    resources = [
        resource
        for resource in inventory.resources
        if resource.logical_id not in {"agents.tasks", "agents.roster"}
    ]
    resources.extend(
        [
            SourceResourceV1(
                logical_id="agents.roster",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.COPY,
                capture_root=roster.parent,
                captured_path=roster,
            ),
            SourceResourceV1(
                logical_id="agents.tasks",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.COPY,
                capture_root=tasks.parent,
                captured_path=tasks,
            ),
            SourceResourceV1(
                logical_id="config.agents",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.CONFIG,
                capture_root=source,
                captured_path=agents_config,
            ),
            SourceResourceV1(
                logical_id="agents.runtime_state",
                kind=ResourceKind.DIRECTORY,
                capture_kind=CaptureKind.COPY,
                capture_root=source,
                captured_path=runtime_root,
            ),
            SourceResourceV1(
                logical_id="keys.root",
                kind=ResourceKind.DIRECTORY,
                capture_kind=CaptureKind.DIRECTORY_ONLY,
                capture_root=keys,
                captured_path=keys,
            ),
            SourceResourceV1(
                logical_id=f"key.{FEEDER_HANDLE}.private",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.KEYPAIR,
                capture_root=keys,
                captured_path=keys / f"{FEEDER_HANDLE}.key",
            ),
            SourceResourceV1(
                logical_id=f"key.{FEEDER_HANDLE}.public",
                kind=ResourceKind.FILE,
                capture_kind=CaptureKind.KEYPAIR,
                capture_root=keys,
                captured_path=keys / f"{FEEDER_HANDLE}.pub",
            ),
        ]
    )
    bindings = list(inventory.field_bindings)
    bindings.extend(
        [
            SourceFieldBindingV1(
                binding_id="agents:data.roster_path",
                config_id="agents",
                field_path=("data", "roster_path"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value="../agents/roster.yaml",
                resolution_base=PathResolutionBase.CONFIG_PARENT,
                resolved_source_path=roster,
                resource_id="agents.roster",
            ),
            SourceFieldBindingV1(
                binding_id="agents:data.keys_dir",
                config_id="agents",
                field_path=("data", "keys_dir"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value="../keys",
                resolution_base=PathResolutionBase.CONFIG_PARENT,
                resolved_source_path=keys,
                resource_id="keys.root",
            ),
            SourceFieldBindingV1(
                binding_id="agents:runtime_state.directory",
                config_id="agents",
                field_path=("runtime_state", "directory"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value="../agent-state",
                resolution_base=PathResolutionBase.CONFIG_PARENT,
                resolved_source_path=runtime_root,
                resource_id="agents.runtime_state",
            ),
            SourceFieldBindingV1(
                binding_id="agents:task_feeder.tasks_file",
                config_id="agents",
                field_path=("task_feeder", "tasks_file"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value="../external-feeds/tasks.jsonl",
                resolution_base=PathResolutionBase.CONFIG_PARENT,
                resolved_source_path=tasks,
                resource_id="agents.tasks",
            ),
        ]
    )
    bindings.extend(
        SourceFieldBindingV1(
            binding_id=f"agents:platform.{field}",
            config_id="agents",
            field_path=("platform", field),
            binding_kind=BindingKind.NETWORK,
            captured_value=value,
            resolution_base=None,
            resolved_source_path=None,
            resource_id=None,
        )
        for field, value in URLS.items()
    )
    feeder_keypair = SourceKeypairV1(
        handle=FEEDER_HANDLE,
        role=KeypairRole.ECONOMIC,
        private_resource_id=f"key.{FEEDER_HANDLE}.private",
        public_resource_id=f"key.{FEEDER_HANDLE}.public",
        expected_public_fingerprint_sha256=fingerprint,
        registration_requirement=RegistrationRequirement.REQUIRED,
        captured_registration_state=CapturedRegistrationState.REGISTERED,
        expected_registered_agent_id=FEEDER_AGENT_ID,
    )
    agents_cwd = source / "agents-runtime"
    agents_cwd.mkdir(mode=0o700)
    updated = inventory.model_copy(
        update={
            "resources": tuple(resources),
            "configs": (
                *inventory.configs,
                SourceConfigV1(
                    logical_id="agents",
                    capture_root=source,
                    captured_path=agents_config,
                    process_cwd=agents_cwd,
                    sha256=digest(config_bytes),
                ),
            ),
            "field_bindings": tuple(bindings),
            "keypairs": (*inventory.keypairs, feeder_keypair),
            "process_ownership": (
                *inventory.process_ownership,
                OwnedProcessV1(
                    logical_id="agents",
                    config_id="agents",
                    process_cwd=agents_cwd,
                    agents_package_root=inventory.agents_package_root,
                    pid=None,
                    pgid=None,
                    sid=None,
                    running=False,
                    group_drained=False,
                    leader_returncode=None,
                    cleanup_error=None,
                ),
            ),
        }
    )
    return Fixture(updated, connection, runtime_root, identity, fingerprint, tasks)


def reseal_backup(artifact: Path) -> None:
    payload = artifact / "payload"
    manifest_path = payload / "manifest.json"
    manifest_bytes = canonical_json_bytes(json.loads(manifest_path.read_bytes()))
    manifest_path.write_bytes(manifest_bytes)
    manifest_digest = digest(manifest_bytes)
    (payload / "manifest.sha256").write_text(f"{manifest_digest}\n")
    marker_path = artifact / "COMPLETE"
    marker = json.loads(marker_path.read_bytes())
    marker["manifest_sha256"] = manifest_digest
    from db_gateway_service import recovery

    marker["payload_sha256"] = recovery._tree_digest(payload, exclude=frozenset())
    marker_path.write_bytes(canonical_json_bytes(marker))


def reseal_restore(target: Path) -> None:
    manifest_path = target / "restore-manifest.json"
    manifest_bytes = canonical_json_bytes(json.loads(manifest_path.read_bytes()))
    manifest_path.write_bytes(manifest_bytes)
    marker_path = target / "COMPLETE"
    marker = json.loads(marker_path.read_bytes())
    marker["manifest_sha256"] = digest(manifest_bytes)
    from db_gateway_service import recovery

    marker["payload_sha256"] = recovery._tree_digest(target, exclude=frozenset({"COMPLETE"}))
    marker_path.write_bytes(canonical_json_bytes(marker))
