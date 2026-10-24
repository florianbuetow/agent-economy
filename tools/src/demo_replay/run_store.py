"""Leased, durable local state for one demo replay run.

Preparation publishes actor keypairs through per-actor staging directories, then
creates one typed empty slot for every declared actor and scenario step.  The
immutable ``run.json`` manifest is published last and is the completion marker;
after it exists, keys, directories, and slots are strictly load-only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Self, cast
from uuid import UUID

import yaml
import httpx
from service_auth.signing import (
    KeyMaterialError,
    generate_keypair,
    keypair_fingerprint,
    load_keypair,
    public_key_to_b64,
)
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import parse_financial_envelope
from service_auth.task_postings import (
    TaskPostingIntent,
    TaskPostingResponse,
    parse_task_posting_status_response,
    parse_task_posting_submit_response,
)
from service_commons.maintenance_lock import (
    HeldMaintenanceLock,
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_maintenance_lock,
)

from demo_replay.run_store_files import (
    RunStoreFileError,
    SafeDirectory,
    decode_record,
    fsync_directory,
    list_names,
    open_private_directory,
    publish_exclusive,
    read_record,
    remove_private_child,
    rename_private_child,
    replace_record,
    validate_regular_file,
)
from demo_replay.task_operations import (
    PHASES,
    TaskIntent,
    completed_bid,
    completed_mutation,
    step_identity,
)

_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REGISTRATION_FIELDS = {"agent_id", "name", "public_key", "registered_at"}
_ACTOR_SLOT_FIELDS = {
    "schema_version",
    "run_scope_sha256",
    "handle",
    "name",
    "public_fingerprint_sha256",
    "state",
    "registration",
}
_STEP_FIELDS = {
    "schema_version",
    "run_scope_sha256",
    "index",
    "step_sha256",
    "action",
    "actor_handle",
    "agent_id",
    "actor_public_fingerprint_sha256",
    "intent",
    "revision",
    "state",
    "result",
    "diagnostic",
}


class RunStoreError(RuntimeError):
    """A stable failure at the durable run boundary."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RunStoreError("invalid_scope", f"{field} must be a nonempty string")
    return value


def _canonical_bytes(value: object, *, reason: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise RunStoreError(reason, "value is not finite canonical JSON") from exc


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _strict_json_equal(left: object, right: object) -> bool:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _strict_json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _strict_json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return type(left) is type(right) and left == right


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class ActorDefinition:
    handle: str
    name: str

    def __post_init__(self) -> None:
        handle = _text(self.handle, "actor handle")
        _text(self.name, "actor name")
        if handle in {".", ".."} or "/" in handle or "\\" in handle:
            raise RunStoreError("invalid_scope", "actor handle is unsafe")


@dataclass(frozen=True, slots=True)
class RunEnvironment:
    environment_id: str
    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str
    platform_public_fingerprint_sha256: str

    def __post_init__(self) -> None:
        for field in (
            "environment_id",
            "identity_url",
            "bank_url",
            "task_board_url",
            "reputation_url",
            "court_url",
        ):
            _text(getattr(self, field), field)
        if _SHA256.fullmatch(self.platform_public_fingerprint_sha256) is None:
            raise RunStoreError("invalid_scope", "platform fingerprint must be lowercase SHA-256")

    def as_json(self) -> dict[str, str]:
        return {
            "environment_id": self.environment_id,
            "identity_url": self.identity_url,
            "bank_url": self.bank_url,
            "task_board_url": self.task_board_url,
            "reputation_url": self.reputation_url,
            "court_url": self.court_url,
            "platform_public_fingerprint_sha256": self.platform_public_fingerprint_sha256,
        }


@dataclass(frozen=True, slots=True)
class StoredActor:
    handle: str
    name: str
    private_key_path: Path
    public_key_path: Path
    public_key: str
    public_fingerprint_sha256: str
    agent_id: str | None
    registration: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class StoredStep:
    index: int
    state: str
    actor_handle: str
    agent_id: str
    actor_public_fingerprint_sha256: str
    intent: TaskPostingIntent | Mapping[str, object]
    revision: int
    result: Mapping[str, object] | None = None
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class _Scope:
    preparation: dict[str, object]
    steps: tuple[dict[str, object], ...]
    actor_definitions: tuple[ActorDefinition, ...]


def _validated_scope(
    *,
    run_id: object,
    environment: object,
    scenario_bytes: object,
    scenario: object,
    actor_definitions: object,
    effective_delay_seconds: object,
    max_record_bytes: object,
    diagnostic_limit: object,
) -> tuple[str, RunEnvironment, bytes, _Scope, float, int, int]:
    if not isinstance(run_id, str) or not run_id.startswith("run-"):
        raise RunStoreError("invalid_scope", "run_id must be run-<canonical UUID>")
    uuid_text = run_id.removeprefix("run-")
    try:
        canonical_uuid = str(UUID(uuid_text))
    except (ValueError, AttributeError) as exc:
        raise RunStoreError("invalid_scope", "run_id must be run-<canonical UUID>") from exc
    if uuid_text != canonical_uuid:
        raise RunStoreError("invalid_scope", "run_id must be run-<canonical UUID>")
    if not isinstance(environment, RunEnvironment):
        raise TypeError("environment must be a RunEnvironment")
    if not isinstance(scenario_bytes, bytes):
        raise TypeError("scenario_bytes must be bytes")
    if not isinstance(scenario, Mapping):
        raise RunStoreError("invalid_scope", "scenario must be an object")
    try:
        parsed = yaml.safe_load(scenario_bytes)
    except yaml.YAMLError as exc:
        raise RunStoreError("invalid_scope", "scenario bytes are not valid YAML") from exc
    supplied_canonical = _canonical_bytes(scenario, reason="invalid_scope")
    if _canonical_bytes(parsed, reason="invalid_scope") != supplied_canonical:
        raise RunStoreError("invalid_scope", "scenario bytes and parsed scenario disagree")
    if not isinstance(actor_definitions, tuple) or not all(
        isinstance(actor, ActorDefinition) for actor in actor_definitions
    ):
        raise TypeError("actor_definitions must be a tuple of ActorDefinition values")
    actors_value = scenario.get("agents")
    steps_value = scenario.get("steps")
    if not isinstance(actors_value, list) or not isinstance(steps_value, list):
        raise RunStoreError("invalid_scope", "scenario agents and steps must be arrays")
    expected_actors: list[dict[str, str]] = []
    for actor in actors_value:
        if not isinstance(actor, Mapping) or set(actor) != {"handle", "name"}:
            raise RunStoreError("invalid_scope", "scenario actor has invalid fields")
        definition = ActorDefinition(
            handle=_text(actor["handle"], "actor handle"),
            name=_text(actor["name"], "actor name"),
        )
        expected_actors.append({"handle": definition.handle, "name": definition.name})
    declared = [{"handle": actor.handle, "name": actor.name} for actor in actor_definitions]
    if declared != expected_actors or len({actor.handle for actor in actor_definitions}) != len(
        actor_definitions
    ):
        raise RunStoreError("invalid_scope", "declared actors do not match scenario actors")
    steps: list[dict[str, object]] = []
    for step in steps_value:
        if not isinstance(step, Mapping) or not isinstance(step.get("action"), str):
            raise RunStoreError("invalid_scope", "scenario step must be a supported object")
        _canonical_bytes(step, reason="invalid_scope")
        steps.append(dict(step))
    if type(effective_delay_seconds) not in {int, float}:
        raise RunStoreError("invalid_scope", "effective delay must be a finite nonnegative number")
    effective_delay = float(cast("int | float", effective_delay_seconds))
    if not math.isfinite(effective_delay) or effective_delay < 0:
        raise RunStoreError("invalid_scope", "effective delay must be a finite nonnegative number")
    if type(max_record_bytes) is not int or max_record_bytes < 1:
        raise RunStoreError("invalid_scope", "max_record_bytes must be a positive integer")
    if type(diagnostic_limit) is not int or diagnostic_limit < 1:
        raise RunStoreError("invalid_scope", "diagnostic_limit must be a positive integer")
    preparation: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "run_id": run_id,
        "environment": environment.as_json(),
        "scenario_raw_sha256": _sha256(scenario_bytes),
        "scenario_raw_size": len(scenario_bytes),
        "scenario_canonical_sha256": _sha256(supplied_canonical),
        "step_sha256s": [_sha256(_canonical_bytes(step, reason="invalid_scope")) for step in steps],
        "step_count": len(steps),
        "effective_delay_seconds": effective_delay,
        "actors": expected_actors,
    }
    return (
        run_id,
        environment,
        scenario_bytes,
        _Scope(preparation, tuple(steps), actor_definitions),
        effective_delay,
        max_record_bytes,
        diagnostic_limit,
    )


class RunStore:
    """One nonblocking leased demo run with immutable durable scope."""

    def __init__(
        self,
        *,
        state_directory: Path,
        run_id: str,
        scope: _Scope,
        max_record_bytes: int,
        diagnostic_limit: int,
    ) -> None:
        self.run_directory = state_directory / "runs" / run_id
        self._state_directory = state_directory
        self._scope = scope
        self._max_record_bytes = max_record_bytes
        self._diagnostic_limit = diagnostic_limit
        self._lease_context: AbstractContextManager[HeldMaintenanceLock] | None = None
        self._lease: HeldMaintenanceLock | None = None
        self._run: SafeDirectory | None = None
        self._actors_directory: SafeDirectory | None = None
        self._steps_directory: SafeDirectory | None = None
        self._keys_directory: SafeDirectory | None = None
        self._scope_sha256 = ""
        self._actors: dict[str, StoredActor] = {}
        self._steps: dict[int, StoredStep | None] = {}
        self._validation_prefix: list[tuple[int, Mapping[str, object], StoredStep]] | None = None

    @classmethod
    def open(
        cls,
        *,
        state_directory: Path,
        run_id: str,
        environment: RunEnvironment,
        scenario_bytes: bytes,
        scenario: Mapping[str, object],
        actor_definitions: tuple[ActorDefinition, ...],
        effective_delay_seconds: float,
        max_record_bytes: int,
        diagnostic_limit: int,
    ) -> Self:
        validated = _validated_scope(
            run_id=run_id,
            environment=environment,
            scenario_bytes=scenario_bytes,
            scenario=scenario,
            actor_definitions=actor_definitions,
            effective_delay_seconds=effective_delay_seconds,
            max_record_bytes=max_record_bytes,
            diagnostic_limit=diagnostic_limit,
        )
        if not isinstance(state_directory, Path) or not state_directory.is_absolute():
            raise RunStoreError("unsafe_path", "state_directory must be an absolute Path")
        store = cls(
            state_directory=state_directory,
            run_id=validated[0],
            scope=validated[3],
            max_record_bytes=validated[5],
            diagnostic_limit=validated[6],
        )
        store._reject_unbound_residue()
        try:
            store._open()
            return store
        except BaseException:
            store._close(None, None, None)
            raise

    def _reject_unbound_residue(self) -> None:
        """Reject unprepared artifacts while allowing the lease-creation race window."""

        try:
            run_stat = self.run_directory.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RunStoreError("unsafe_path", "run directory cannot be inspected") from exc
        if not stat.S_ISDIR(run_stat.st_mode) or stat.S_ISLNK(run_stat.st_mode):
            raise RunStoreError("unsafe_path", "run path is not a real directory")
        preparation = self.run_directory / "preparation.json"
        try:
            preparation.lstat()
            return
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RunStoreError("unsafe_path", "preparation path cannot be inspected") from exc
        try:
            names = {entry.name for entry in self.run_directory.iterdir()}
        except OSError as exc:
            raise RunStoreError("unsafe_path", "run directory cannot be scanned") from exc
        if names and names != {"runtime.lock"}:
            raise RunStoreError("unbound_residue", "run artifacts lack preparation binding")

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close(exc_type, exc, traceback)

    @property
    def actors(self) -> Mapping[str, StoredActor]:
        self._require_open()
        return MappingProxyType(dict(self._actors))

    def _translate(self, exc: BaseException) -> RunStoreError:
        if isinstance(exc, RunStoreError):
            return exc
        reason = getattr(exc, "reason", "storage_failure")
        if isinstance(exc, MaintenanceLockError):
            reason = "lock_unavailable" if reason == "busy" else reason
        if reason not in {
            "lock_unavailable",
            "unsafe_path",
            "unsafe_mode",
            "record_too_large",
            "corrupt_record",
        }:
            reason = "storage_failure"
        return RunStoreError(reason, str(exc))

    def _open(self) -> None:
        try:
            self._run = open_private_directory(
                self.run_directory,
                create=True,
                private_root=self._state_directory,
            )
            lease = hold_maintenance_lock(
                self.run_directory / "runtime.lock",
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
            )
            self._lease_context = lease
            self._lease = lease.__enter__()
            validate_regular_file(self._run, "runtime.lock", expected_mode=0o600)
            existing_names = set(list_names(self._run))
            preparation = self._read_json(self._run, "preparation.json")
            if preparation is None:
                if existing_names - {"runtime.lock"}:
                    raise RunStoreError("unbound_residue", "run artifacts lack preparation binding")
                self._publish_json(self._run, "preparation.json", self._scope.preparation)
                preparation = self._read_json(self._run, "preparation.json")
            if not _strict_json_equal(preparation, self._scope.preparation):
                reason = (
                    "corrupt_record" if preparation == self._scope.preparation else "scope_conflict"
                )
                raise RunStoreError(reason, "prepared run scope differs")

            allowed_run_names = {
                "runtime.lock",
                "preparation.json",
                "run.json",
                "keys",
                "actors",
                "steps",
            }
            if set(list_names(self._run)) - allowed_run_names:
                raise RunStoreError("corrupt_record", "run directory contains unknown residue")
            current_manifest = self._read_json(self._run, "run.json")
            published = current_manifest is not None
            if published and not {"keys", "actors", "steps"}.issubset(list_names(self._run)):
                raise RunStoreError("corrupt_record", "published run is missing required storage")

            self._keys_directory = open_private_directory(
                self.run_directory / "keys",
                create=not published,
                private_root=self._state_directory,
            )
            self._load_actor_keys(published=published)
            manifest_scope = self._manifest_scope()
            self._scope_sha256 = _sha256(_canonical_bytes(manifest_scope, reason="storage_failure"))
            manifest = self._manifest(manifest_scope)

            if published:
                if not _strict_json_equal(current_manifest, manifest):
                    reason = "corrupt_record" if current_manifest == manifest else "scope_conflict"
                    raise RunStoreError(reason, "immutable run manifest differs")
                fsync_directory(self._run)
                self._actors_directory = open_private_directory(
                    self.run_directory / "actors",
                    create=False,
                    private_root=self._state_directory,
                )
                self._steps_directory = open_private_directory(
                    self.run_directory / "steps",
                    create=False,
                    private_root=self._state_directory,
                )
                self._load_actor_slots()
                self._load_step_slots()
                return

            self._actors_directory = open_private_directory(
                self.run_directory / "actors", create=True, private_root=self._state_directory
            )
            self._steps_directory = open_private_directory(
                self.run_directory / "steps", create=True, private_root=self._state_directory
            )
            self._prepare_empty_actor_slots()
            self._prepare_empty_step_slots()
            self._publish_json(self._run, "run.json", manifest)
            current_manifest = self._read_json(self._run, "run.json")
            if not _strict_json_equal(current_manifest, manifest):
                raise RunStoreError("corrupt_record", "published run manifest differs")
            fsync_directory(self._run)
        except (RunStoreFileError, KeyMaterialError, MaintenanceLockError, OSError) as exc:
            raise self._translate(exc) from exc

    def _require_open(self) -> None:
        if self._run is None or self._lease is None or self._lease_context is None:
            raise RunStoreError("storage_failure", "run store is closed")
        try:
            self._lease.verify_identity()
            self._run.verify()
            validate_regular_file(self._run, "runtime.lock", expected_mode=0o600)
            if self._keys_directory is not None:
                self._keys_directory.verify()
            if self._actors_directory is not None:
                self._actors_directory.verify()
            if self._steps_directory is not None:
                self._steps_directory.verify()
        except (RunStoreFileError, MaintenanceLockError) as exc:
            raise self._translate(exc) from exc

    def _close(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        lease = self._lease_context
        self._lease_context = None
        self._lease = None
        release_error: BaseException | None = None
        if lease is not None:
            try:
                lease.__exit__(exc_type, exc, traceback)
            except BaseException as error:
                release_error = error
        for attribute in ("_steps_directory", "_actors_directory", "_keys_directory", "_run"):
            directory = cast("SafeDirectory | None", getattr(self, attribute))
            setattr(self, attribute, None)
            if directory is not None:
                try:
                    directory.close()
                except BaseException as error:
                    if release_error is None:
                        release_error = error
        if release_error is not None and exc is None:
            raise self._translate(release_error) from release_error

    def _read_json(self, directory: SafeDirectory, name: str) -> object | None:
        content = read_record(directory, name, max_record_bytes=self._max_record_bytes)
        if content is None:
            return None
        value = decode_record(content, name=name)
        if _canonical_bytes(value, reason="corrupt_record") != content:
            raise RunStoreError("corrupt_record", f"record is not canonical JSON: {name}")
        return value

    def _publish_json(self, directory: SafeDirectory, name: str, value: object) -> None:
        content = _canonical_bytes(value, reason="storage_failure")
        if len(content) > self._max_record_bytes:
            raise RunStoreError("record_too_large", f"record exceeds configured limit: {name}")
        try:
            published = publish_exclusive(directory, name, content)
        except RunStoreFileError as exc:
            raise self._translate(exc) from exc
        if not published:
            current = self._read_json(directory, name)
            if not _strict_json_equal(current, value):
                raise RunStoreError("corrupt_record", f"record publication conflicted: {name}")

    def _manifest_scope(self) -> dict[str, object]:
        return {
            "preparation": self._scope.preparation,
            "actors": [
                {
                    "handle": actor.handle,
                    "name": actor.name,
                    "public_fingerprint_sha256": actor.public_fingerprint_sha256,
                }
                for actor in self._actors.values()
            ],
        }

    def _manifest(self, manifest_scope: Mapping[str, object]) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "run_id": self._scope.preparation["run_id"],
            "preparation_sha256": _sha256(
                _canonical_bytes(self._scope.preparation, reason="storage_failure")
            ),
            "actors": manifest_scope["actors"],
            "scope_sha256": self._scope_sha256,
        }

    def _load_actor_keys(self, *, published: bool) -> None:
        assert self._keys_directory is not None
        expected_names = {actor.handle for actor in self._scope.actor_definitions}
        expected_names.update(
            f".{actor.handle}.provisioning" for actor in self._scope.actor_definitions
        )
        observed_names = set(list_names(self._keys_directory))
        if observed_names - expected_names:
            raise RunStoreError("corrupt_record", "keys directory contains unknown residue")
        actors: dict[str, StoredActor] = {}
        for definition in self._scope.actor_definitions:
            final_name = definition.handle
            staging_name = f".{definition.handle}.provisioning"
            final_present = final_name in observed_names
            staging_present = staging_name in observed_names
            if published:
                if not final_present or staging_present:
                    raise RunStoreError("corrupt_record", "published actor keypair is incomplete")
            elif final_present and staging_present:
                raise RunStoreError("corrupt_record", "actor has final and staging key directories")
            elif not final_present:
                self._finish_actor_provisioning(definition, staging_present=staging_present)

            actor_key_directory = self._keys_directory.path / final_name
            opened = open_private_directory(
                actor_key_directory,
                create=False,
                private_root=self._state_directory,
            )
            try:
                self._validate_complete_actor_key_directory(opened, definition.handle)
            finally:
                opened.close()
            _private_key, public_key = load_keypair(definition.handle, actor_key_directory)
            fingerprint = keypair_fingerprint(
                definition.handle,
                actor_key_directory,
                allow_legacy_private_mode=False,
            )
            actors[definition.handle] = StoredActor(
                handle=definition.handle,
                name=definition.name,
                private_key_path=actor_key_directory / f"{definition.handle}.key",
                public_key_path=actor_key_directory / f"{definition.handle}.pub",
                public_key=f"ed25519:{public_key_to_b64(public_key)}",
                public_fingerprint_sha256=fingerprint,
                agent_id=None,
                registration=None,
            )
        if published and set(list_names(self._keys_directory)) != {
            actor.handle for actor in self._scope.actor_definitions
        }:
            raise RunStoreError("corrupt_record", "published keys namespace differs")
        self._actors = actors

    def _finish_actor_provisioning(
        self,
        definition: ActorDefinition,
        *,
        staging_present: bool,
    ) -> None:
        assert self._keys_directory is not None
        staging_name = f".{definition.handle}.provisioning"
        staging_path = self._keys_directory.path / staging_name
        if staging_present:
            staging = open_private_directory(
                staging_path,
                create=False,
                private_root=self._state_directory,
            )
            observed = tuple(list_names(staging))
            try:
                if self._staging_is_complete(staging, definition.handle, observed):
                    fsync_directory(staging)
                    rename_private_child(self._keys_directory, staging, definition.handle)
                    staging.close()
                    return
                self._validate_incomplete_staging(staging, definition.handle, observed)
            except BaseException:
                staging.close()
                raise
            remove_private_child(self._keys_directory, staging, names=observed)

        staging = open_private_directory(
            staging_path,
            create=True,
            private_root=self._state_directory,
        )
        try:
            generate_keypair(definition.handle, staging.path)
            self._validate_complete_actor_key_directory(staging, definition.handle)
            fsync_directory(staging)
        except BaseException:
            staging.close()
            raise
        rename_private_child(self._keys_directory, staging, definition.handle)
        staging.close()

    def _staging_is_complete(
        self,
        staging: SafeDirectory,
        handle: str,
        observed: tuple[str, ...],
    ) -> bool:
        expected = {f"{handle}.key", f"{handle}.pub", f"{handle}.lock"}
        if set(observed) != expected:
            return False
        try:
            self._validate_complete_actor_key_directory(staging, handle)
            load_keypair(handle, staging.path)
        except (RunStoreFileError, KeyMaterialError) as exc:
            raise RunStoreError("corrupt_record", "complete staging keypair is malformed") from exc
        return True

    def _validate_incomplete_staging(
        self,
        staging: SafeDirectory,
        handle: str,
        observed: tuple[str, ...],
    ) -> None:
        exact_modes = {
            f"{handle}.key": 0o600,
            f"{handle}.pub": 0o644,
            f"{handle}.lock": 0o600,
        }
        private_temporary = re.compile(rf"^\.{re.escape(handle)}\.private-.+\.tmp$")
        public_temporary = re.compile(rf"^\.{re.escape(handle)}\.public-.+\.tmp$")
        for name in observed:
            mode = exact_modes.get(name)
            if mode is None and private_temporary.fullmatch(name) is not None:
                mode = 0o600
            elif mode is None and public_temporary.fullmatch(name) is not None:
                mode = 0o644
            if mode is None:
                raise RunStoreError("corrupt_record", "staging key directory has unknown residue")
            validate_regular_file(staging, name, expected_mode=mode)

    def _validate_complete_actor_key_directory(
        self,
        directory: SafeDirectory,
        handle: str,
    ) -> None:
        expected = {f"{handle}.key", f"{handle}.pub", f"{handle}.lock"}
        observed = set(list_names(directory))
        if not expected.issubset(observed):
            raise RunStoreError("corrupt_record", "actor key directory is incomplete")
        validate_regular_file(directory, f"{handle}.key", expected_mode=0o600)
        validate_regular_file(directory, f"{handle}.pub", expected_mode=0o644)
        validate_regular_file(directory, f"{handle}.lock", expected_mode=0o600)
        if observed != expected:
            raise RunStoreError("corrupt_record", "actor key directory is incomplete")

    def _empty_actor_record(self, actor: StoredActor) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "run_scope_sha256": self._scope_sha256,
            "handle": actor.handle,
            "name": actor.name,
            "public_fingerprint_sha256": actor.public_fingerprint_sha256,
            "state": "empty",
            "registration": None,
        }

    def _actor_record(self, actor: StoredActor) -> dict[str, object]:
        record = self._empty_actor_record(actor)
        if actor.registration is not None:
            record["state"] = "bound"
            record["registration"] = dict(actor.registration)
        return record

    def _prepare_empty_actor_slots(self) -> None:
        assert self._actors_directory is not None
        expected_names = {f"{handle}.json" for handle in self._actors}
        observed_names = set(list_names(self._actors_directory))
        if observed_names - expected_names:
            raise RunStoreError("corrupt_record", "actor directory contains unknown record")
        for handle, actor in self._actors.items():
            name = f"{handle}.json"
            expected = self._empty_actor_record(actor)
            current = self._read_json(self._actors_directory, name)
            if current is None:
                self._publish_json(self._actors_directory, name, expected)
                current = self._read_json(self._actors_directory, name)
            if not _strict_json_equal(current, expected):
                raise RunStoreError("corrupt_record", "pre-manifest actor slot is not empty")

    def _load_actor_slots(self) -> None:
        assert self._actors_directory is not None
        expected_names = {f"{handle}.json" for handle in self._actors}
        if set(list_names(self._actors_directory)) != expected_names:
            raise RunStoreError("corrupt_record", "published actor slots differ")
        for handle, actor in tuple(self._actors.items()):
            value = self._read_json(self._actors_directory, f"{handle}.json")
            if value is None:
                raise RunStoreError("corrupt_record", "actor slot is missing")
            self._actors[handle] = self._decode_actor_slot(actor, value)

    def _decode_actor_slot(self, actor: StoredActor, value: object) -> StoredActor:
        if not isinstance(value, Mapping) or set(value) != _ACTOR_SLOT_FIELDS:
            raise RunStoreError("corrupt_record", "actor slot fields differ")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != _SCHEMA_VERSION
            or value["run_scope_sha256"] != self._scope_sha256
            or value["handle"] != actor.handle
            or value["name"] != actor.name
            or value["public_fingerprint_sha256"] != actor.public_fingerprint_sha256
        ):
            raise RunStoreError("corrupt_record", "actor slot binding differs")
        if value["state"] == "empty" and value["registration"] is None:
            return replace(actor, agent_id=None, registration=None)
        if value["state"] != "bound":
            raise RunStoreError("corrupt_record", "actor slot state is invalid")
        try:
            registration = self._validate_registration(actor, value["registration"])
        except RunStoreError as exc:
            raise RunStoreError(
                "corrupt_record", "stored registration does not bind actor"
            ) from exc
        return replace(
            actor,
            agent_id=cast("str", registration["agent_id"]),
            registration=_freeze_mapping(registration),
        )

    def _read_actor_slot(self, actor_handle: str) -> StoredActor:
        actor = self._actors.get(actor_handle)
        if actor is None:
            raise RunStoreError("registration_conflict", "registration actor is unknown")
        assert self._actors_directory is not None
        value = self._read_json(self._actors_directory, f"{actor_handle}.json")
        if value is None:
            raise RunStoreError("corrupt_record", "actor slot is missing")
        base_actor = replace(actor, agent_id=None, registration=None)
        current = self._decode_actor_slot(base_actor, value)
        if current != actor:
            raise RunStoreError("corrupt_record", "actor slot disagrees with cached registration")
        return current

    def _validate_registration(self, actor: StoredActor, response: object) -> dict[str, object]:
        if not isinstance(response, Mapping) or set(response) != _REGISTRATION_FIELDS:
            raise RunStoreError("registration_conflict", "registration fields differ")
        copied = dict(response)
        if (
            not isinstance(copied["agent_id"], str)
            or not copied["agent_id"]
            or copied["name"] != actor.name
            or copied["public_key"] != actor.public_key
            or not isinstance(copied["registered_at"], str)
            or not copied["registered_at"]
        ):
            raise RunStoreError("registration_conflict", "registration does not bind actor key")
        _canonical_bytes(copied, reason="registration_conflict")
        return copied

    def record_registration(
        self, *, actor_handle: str, response: Mapping[str, object]
    ) -> StoredActor:
        self._require_open()
        actor = self._read_actor_slot(actor_handle)
        registration = self._validate_registration(actor, response)
        if actor.registration is not None:
            if dict(actor.registration) != registration:
                raise RunStoreError("registration_conflict", "durable registration differs")
            return actor
        bound = replace(
            actor,
            agent_id=cast("str", registration["agent_id"]),
            registration=_freeze_mapping(registration),
        )
        assert self._actors_directory is not None
        name = f"{actor_handle}.json"
        content = _canonical_bytes(self._actor_record(bound), reason="storage_failure")
        if len(content) > self._max_record_bytes:
            raise RunStoreError("record_too_large", "registration record exceeds configured limit")
        try:
            replace_record(self._actors_directory, name, content)
        except RunStoreFileError as exc:
            raise self._translate(exc) from exc
        value = self._read_json(self._actors_directory, name)
        restored = self._decode_actor_slot(replace(actor, agent_id=None, registration=None), value)
        if restored != bound:
            raise RunStoreError("corrupt_record", "registration readback differs")
        self._actors[actor_handle] = restored
        return restored

    def _step_name(self, index: int) -> str:
        return f"{index:06d}.json"

    def _empty_step_record(self, index: int) -> dict[str, object]:
        hashes = cast("list[str]", self._scope.preparation["step_sha256s"])
        return {
            "schema_version": _SCHEMA_VERSION,
            "run_scope_sha256": self._scope_sha256,
            "index": index,
            "step_sha256": hashes[index],
            "action": self._scope.steps[index]["action"],
            "actor_handle": None,
            "agent_id": None,
            "actor_public_fingerprint_sha256": None,
            "intent": None,
            "revision": 0,
            "state": "empty",
            "result": None,
            "diagnostic": None,
        }

    def _prepare_empty_step_slots(self) -> None:
        assert self._steps_directory is not None
        expected_names = {self._step_name(index) for index in range(len(self._scope.steps))}
        observed_names = set(list_names(self._steps_directory))
        if observed_names - expected_names:
            raise RunStoreError("corrupt_record", "steps directory contains unknown record")
        self._steps = {}
        for index in range(len(self._scope.steps)):
            name = self._step_name(index)
            expected = self._empty_step_record(index)
            value = self._read_json(self._steps_directory, name)
            if value is None:
                self._publish_json(self._steps_directory, name, expected)
                value = self._read_json(self._steps_directory, name)
            if not _strict_json_equal(value, expected):
                raise RunStoreError("corrupt_record", "pre-manifest step slot is not empty")
            self._steps[index] = None

    def _load_step_slots(self) -> None:
        assert self._steps_directory is not None
        expected_names = {self._step_name(index) for index in range(len(self._scope.steps))}
        self._steps = {}
        prefix: list[tuple[int, Mapping[str, object], StoredStep]] = []
        previous = self._validation_prefix
        self._validation_prefix = prefix
        try:
            for index in range(len(self._scope.steps)):
                name = self._step_name(index)
                value = self._read_json(self._steps_directory, name)
                if value is None:
                    raise RunStoreError("corrupt_record", "step slot is missing")
                decoded = self._decode_step(value)
                self._steps[index] = decoded
                if decoded is not None and decoded.state == "completed":
                    prefix.append((index, self._scope.steps[index], decoded))
        finally:
            self._validation_prefix = previous
        if set(list_names(self._steps_directory)) != expected_names:
            raise RunStoreError("corrupt_record", "published step slots differ")

    def _decode_step(self, value: object) -> StoredStep | None:
        if not isinstance(value, Mapping) or set(value) != _STEP_FIELDS:
            raise RunStoreError("corrupt_record", "step record fields differ")
        index = value["index"]
        if type(index) is not int or not 0 <= index < len(self._scope.steps):
            raise RunStoreError("corrupt_record", "step index is invalid")
        if _strict_json_equal(value, self._empty_step_record(index)):
            return None
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != _SCHEMA_VERSION
            or value["run_scope_sha256"] != self._scope_sha256
        ):
            raise RunStoreError("corrupt_record", "step scope differs")
        revision = value["revision"]
        if type(revision) is not int or revision not in {1, 2, 3}:
            raise RunStoreError("corrupt_record", "step revision is invalid")
        state = value["state"]
        expected_revision = {
            "prepared": 1,
            "dispatching": 2,
            "completed": 3,
            "definitive_rejection": 3,
            "attention_required": 3,
        }.get(cast("str", state))
        if expected_revision is None or revision != expected_revision:
            raise RunStoreError("corrupt_record", "step state and revision disagree")
        scenario_step = self._scope.steps[index]
        expected_hash = cast("list[str]", self._scope.preparation["step_sha256s"])[index]
        actor_handle = value["actor_handle"]
        actor = self._actors.get(cast("str", actor_handle))
        if (
            value["step_sha256"] != expected_hash
            or value["action"] != scenario_step.get("action")
            or actor is None
            or actor.agent_id is None
            or value["agent_id"] != actor.agent_id
            or value["actor_public_fingerprint_sha256"] != actor.public_fingerprint_sha256
        ):
            raise RunStoreError("corrupt_record", "step binding differs")
        if not isinstance(value["intent"], Mapping):
            raise RunStoreError("corrupt_record", "stored step intent is invalid")
        if scenario_step.get("action") == "post_task":
            if scenario_step.get("poster") != actor_handle:
                raise RunStoreError("corrupt_record", "stored posting owner differs")
            try:
                parsed_intent = TaskPostingIntent.from_json(value["intent"])
            except (TypeError, ValueError) as exc:
                raise RunStoreError("corrupt_record", "stored posting intent is invalid") from exc
            expected_terms = {
                "title": scenario_step.get("title"),
                "spec": scenario_step.get("spec"),
                "reward": scenario_step.get("reward"),
                "bidding_deadline_seconds": scenario_step.get("bidding_deadline_seconds"),
                "acceptance_window_seconds": scenario_step.get("acceptance_window_seconds"),
                "execution_deadline_seconds": scenario_step.get("execution_deadline_seconds"),
                "review_deadline_seconds": scenario_step.get("review_deadline_seconds"),
            }
            actual_terms = {field: getattr(parsed_intent, field) for field in expected_terms}
            if parsed_intent.actor_id != actor.agent_id or actual_terms != expected_terms:
                raise RunStoreError(
                    "corrupt_record", "stored posting intent ownership or terms differ"
                )
            intent: TaskPostingIntent | Mapping[str, object] = parsed_intent
        else:
            intent = _freeze_mapping(dict(value["intent"]))
        result_value = value["result"]
        if state in {"prepared", "dispatching"}:
            if result_value is not None or value["diagnostic"] is not None:
                raise RunStoreError("corrupt_record", "unfinished step has terminal evidence")
            result = None
            diagnostic = None
        else:
            if not isinstance(result_value, Mapping):
                raise RunStoreError("corrupt_record", "terminal step result is invalid")
            result = _freeze_mapping(dict(result_value))
            diagnostic_value = value["diagnostic"]
            if diagnostic_value is not None and not isinstance(diagnostic_value, str):
                raise RunStoreError("corrupt_record", "step diagnostic is invalid")
            diagnostic = cast("str | None", diagnostic_value)
        if isinstance(intent, TaskPostingIntent) and state in {"completed", "attention_required"}:
            parsed = self._validate_posting_result(intent, result)
            publication_state = self._posting_state(parsed)
            expected_state = "published" if state == "completed" else "blocked_integrity"
            if publication_state != expected_state:
                raise RunStoreError("corrupt_record", "posting terminal state differs")
        elif not isinstance(intent, TaskPostingIntent):
            self._validate_generic_step(
                index=index,
                scenario_step=scenario_step,
                actor=actor,
                intent=intent,
                state=cast("str", state),
                result=result,
            )
        return StoredStep(
            index=index,
            state=cast("str", state),
            actor_handle=cast("str", actor_handle),
            agent_id=actor.agent_id,
            actor_public_fingerprint_sha256=actor.public_fingerprint_sha256,
            intent=intent,
            revision=revision,
            result=result,
            diagnostic=diagnostic,
        )

    def _prior_completed(self, index: int) -> list[tuple[int, Mapping[str, object], StoredStep]]:
        if self._validation_prefix is not None:
            return [item for item in self._validation_prefix if item[0] < index]
        completed: list[tuple[int, Mapping[str, object], StoredStep]] = []
        for prior_index in range(index):
            prior = self._read_step_slot(prior_index)
            if prior is not None and prior.state == "completed" and prior.result is not None:
                completed.append((prior_index, self._scope.steps[prior_index], prior))
        return completed

    def _decode_with_fresh_prefix(self, index: int, value: object) -> StoredStep | None:
        assert self._steps_directory is not None
        prefix: list[tuple[int, Mapping[str, object], StoredStep]] = []
        previous = self._validation_prefix
        self._validation_prefix = prefix
        try:
            for prior_index in range(index):
                prior_value = self._read_json(self._steps_directory, self._step_name(prior_index))
                if prior_value is None:
                    raise RunStoreError("corrupt_record", "dependency step slot is missing")
                prior = self._decode_step(prior_value)
                if prior is not None and prior.state == "completed":
                    prefix.append((prior_index, self._scope.steps[prior_index], prior))
            return self._decode_step(value)
        finally:
            self._validation_prefix = previous

    @staticmethod
    def _posting_task_id(step: StoredStep) -> str:
        if step.result is None:
            raise RunStoreError("dependency_missing", "posting producer has no result")
        body = step.result.get("body")
        if not isinstance(body, Mapping):
            raise RunStoreError("dependency_missing", "posting producer result is invalid")
        task_id = body.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise RunStoreError("dependency_missing", "posting producer task is invalid")
        return task_id

    def _resolve_task_dependency(
        self, index: int, scenario_step: Mapping[str, object], actor_handle: str
    ) -> str:
        prior = self._prior_completed(index)
        task_ref = scenario_step.get("task_ref")
        if task_ref is not None:
            matches = [
                record
                for _position, step, record in prior
                if step.get("action") == "post_task" and step.get("ref") == task_ref
            ]
            if len(matches) != 1:
                raise RunStoreError(
                    "dependency_missing", "referenced posting producer is not completed"
                )
            return self._posting_task_id(matches[0])
        poster_handle = scenario_step.get("poster")
        if isinstance(poster_handle, str):
            matches = [
                record
                for _position, step, record in prior
                if step.get("action") == "post_task" and step.get("poster") == poster_handle
            ]
            if not matches:
                raise RunStoreError("dependency_missing", "poster has no completed posting")
            return self._posting_task_id(matches[-1])
        actor_postings = [
            record
            for _position, step, record in prior
            if step.get("action") == "post_task" and step.get("poster") == actor_handle
        ]
        if actor_postings:
            return self._posting_task_id(actor_postings[-1])
        assignment_matches = [
            record
            for _position, step, record in prior
            if step.get("action") == "accept_bid"
            and record.result is not None
            and record.result.get("bidder_handle") == actor_handle
        ]
        if assignment_matches:
            assignment_result = cast("Mapping[str, object]", assignment_matches[-1].result)
            task_id = assignment_result.get("task_id")
            if isinstance(task_id, str) and task_id:
                return task_id
        posting_matches = [
            record for _position, step, record in prior if step.get("action") == "post_task"
        ]
        if posting_matches:
            return self._posting_task_id(posting_matches[-1])
        raise RunStoreError("dependency_missing", "task producer is not completed")

    def _resolve_dispute_dependency(self, index: int, task_id: str) -> str:
        matches = [
            record
            for _position, step, record in self._prior_completed(index)
            if step.get("action") in {"dispute", "file_claim"}
            and record.result is not None
            and record.result.get("task_id") == task_id
        ]
        if not matches:
            raise RunStoreError("dependency_missing", "dispute producer is not completed")
        dispute_result = cast("Mapping[str, object]", matches[-1].result)
        dispute_id = dispute_result.get("dispute_id")
        if not isinstance(dispute_id, str) or not dispute_id:
            raise RunStoreError("dependency_missing", "dispute producer result is invalid")
        return dispute_id

    def _validate_native_dependencies(
        self,
        *,
        index: int,
        scenario_step: Mapping[str, object],
        actor: StoredActor,
        intent: TaskIntent,
    ) -> None:
        action = intent.action
        role_field = {
            "bid": "bidder",
            "accept_bid": "poster",
            "submit_deliverable": "worker",
            "approve": "poster",
            "dispute": "poster",
        }[action]
        if scenario_step.get(role_field) != actor.handle:
            raise RunStoreError("corrupt_record", "stored task actor role differs")
        arguments = intent.arguments()
        expected_arguments: dict[str, object]
        if action == "bid":
            expected_arguments = {"amount": scenario_step.get("amount")}
        elif action == "accept_bid":
            bidder_handle = scenario_step.get("bidder")
            bidder = self._actors.get(cast("str", bidder_handle))
            if bidder is None or bidder.agent_id is None:
                raise RunStoreError("dependency_missing", "accepted bidder is unregistered")
            expected_arguments = {
                "bidder_handle": bidder_handle,
                "worker_id": bidder.agent_id,
            }
        elif action == "dispute":
            expected_arguments = {"reason": scenario_step.get("reason")}
        else:
            expected_arguments = {}
        for field, expected in expected_arguments.items():
            if type(arguments.get(field)) is not type(expected) or arguments.get(field) != expected:
                raise RunStoreError("corrupt_record", "stored task arguments differ")
        if action != "accept_bid" and set(arguments) != set(expected_arguments):
            raise RunStoreError("corrupt_record", "stored task arguments fields differ")
        expected_task_id = self._resolve_task_dependency(index, scenario_step, actor.handle)
        if intent.task_id != expected_task_id:
            raise RunStoreError("dependency_missing", "stored task producer differs")
        posting_records = [
            record
            for _position, step, record in self._prior_completed(index)
            if step.get("action") == "post_task"
            and self._posting_task_id(record) == expected_task_id
        ]
        if len(posting_records) != 1 or posting_records[0].result is None:
            raise RunStoreError("dependency_missing", "posting producer is ambiguous")
        posting_body = posting_records[0].result.get("body")
        posting_result = posting_body.get("result") if isinstance(posting_body, Mapping) else None
        if not isinstance(posting_result, Mapping):
            raise RunStoreError("dependency_missing", "posting producer facts are invalid")
        observed = intent.observed_task()
        expected_version = {
            "bid": 0,
            "accept_bid": 0,
            "submit_deliverable": 1,
            "approve": 2,
            "dispute": 2,
        }[action]
        if (
            observed.get("task_id") != expected_task_id
            or observed.get("status") != PHASES[action][1]
            or intent.expected_lifecycle_version != expected_version
            or observed.get("lifecycle_version") != expected_version
            or observed.get("poster_id") != posting_result.get("poster_id")
            or observed.get("escrow_id") != posting_result.get("escrow_id")
            or observed.get("reward") != posting_result.get("reward")
            or observed.get("pending_transition") is not None
            or observed.get("legacy_reconciliations") != []
        ):
            raise RunStoreError("corrupt_record", "stored observed task differs")
        if action == "accept_bid":
            prior_bids = [
                record
                for _position, step, record in self._prior_completed(index)
                if step.get("action") == "bid"
                and record.result is not None
                and record.result.get("task_id") == expected_task_id
                and isinstance(record.result.get("bid"), Mapping)
                and cast("Mapping[str, object]", record.result["bid"]).get("bidder_id")
                == arguments.get("worker_id")
            ]
            if len(prior_bids) != 1:
                raise RunStoreError("dependency_missing", "selected bid producer is not completed")
            bid_result = cast("Mapping[str, object]", prior_bids[0].result)
            bid = cast("Mapping[str, object]", bid_result["bid"])
            for field, expected in {
                "bid_id": bid.get("bid_id"),
                "bid_amount": bid.get("amount"),
            }.items():
                if arguments.get(field) != expected:
                    raise RunStoreError("corrupt_record", "stored selected bid differs")
            if set(arguments) != {"bid_id", "worker_id", "bidder_handle", "bid_amount"}:
                raise RunStoreError("corrupt_record", "stored acceptance arguments fields differ")
        if action in {"submit_deliverable", "approve", "dispute"}:
            assignments = [
                record
                for _position, step, record in self._prior_completed(index)
                if step.get("action") == "accept_bid"
                and record.result is not None
                and record.result.get("task_id") == expected_task_id
            ]
            if len(assignments) != 1:
                raise RunStoreError("dependency_missing", "assignment producer is not completed")
            assignment_result = cast("Mapping[str, object]", assignments[0].result)
            response = assignment_result.get("response")
            operation = response.get("operation") if isinstance(response, Mapping) else None
            accepted = operation.get("result") if isinstance(operation, Mapping) else None
            worker_id = accepted.get("worker_id") if isinstance(accepted, Mapping) else None
            if (
                not isinstance(worker_id, str)
                or observed.get("worker_id") != worker_id
                or observed.get("accepted_reward") != posting_result.get("reward")
            ):
                raise RunStoreError("corrupt_record", "stored assignment facts differ")

    def _validate_generic_step(
        self,
        *,
        index: int,
        scenario_step: Mapping[str, object],
        actor: StoredActor,
        intent: Mapping[str, object],
        state: str,
        result: Mapping[str, object] | None,
    ) -> None:
        action = scenario_step.get("action")
        if action == "fund":
            if scenario_step.get("agent") != actor.handle:
                raise RunStoreError("corrupt_record", "funding actor role differs")
            if actor.agent_id is None:
                raise RunStoreError("corrupt_record", "funding actor is unregistered")
            reference = scenario_step.get("reference")
            amount = scenario_step.get("amount")
            expected = {
                "operation_id": operation_id_for(
                    "demo-funding", (actor.agent_id, cast("str", reference))
                ),
                "account_id": actor.agent_id,
                "amount": amount,
                "reference": reference,
            }
            if not _strict_json_equal(intent, expected):
                raise RunStoreError("corrupt_record", "stored funding intent differs")
            if state == "completed":
                try:
                    parsed = parse_financial_envelope(
                        dict(result) if result is not None else result,
                        cast("str", expected["operation_id"]),
                        "credit",
                        {
                            "account_id": actor.agent_id,
                            "amount": cast("int", amount),
                            "reference": cast("str", reference),
                        },
                    )
                except (TypeError, ValueError, RuntimeError) as exc:
                    raise RunStoreError(
                        "corrupt_record", "stored funding receipt is invalid"
                    ) from exc
                if parsed.get("outcome") != "native_receipt":
                    raise RunStoreError("corrupt_record", "stored funding is not a native receipt")
            return
        if action in PHASES:
            try:
                native_intent = TaskIntent.from_json(dict(intent))
            except (TypeError, ValueError) as exc:
                raise RunStoreError("corrupt_record", "stored task intent is invalid") from exc
            if (
                actor.agent_id is None
                or native_intent.action != action
                or native_intent.step_json != step_identity(dict(scenario_step))
                or native_intent.actor_handle != actor.handle
                or native_intent.actor_id != actor.agent_id
                or native_intent.actor_public_key != actor.public_key
            ):
                raise RunStoreError("corrupt_record", "stored task intent binding differs")
            self._validate_native_dependencies(
                index=index,
                scenario_step=scenario_step,
                actor=actor,
                intent=native_intent,
            )
            if state != "completed":
                return
            if result is None or result.get("task_id") != native_intent.task_id:
                raise RunStoreError("corrupt_record", "stored task result identity differs")
            try:
                if action == "bid":
                    if set(result) != {"task_id", "bid"}:
                        raise RuntimeError("bid result fields differ")
                    completed_bid(native_intent, result["bid"])
                else:
                    required = {"task_id", "response"}
                    if action == "accept_bid":
                        required.add("bidder_handle")
                    elif action == "dispute":
                        required.add("dispute_id")
                    if set(result) != required:
                        raise RuntimeError("task result fields differ")
                    completed, _current = completed_mutation(native_intent, result["response"])
                    if action == "accept_bid" and result.get("bidder_handle") != (
                        native_intent.arguments().get("bidder_handle")
                    ):
                        raise RuntimeError("assignment bidder differs")
                    if action == "dispute" and result.get("dispute_id") != completed.get(
                        "dispute_id"
                    ):
                        raise RuntimeError("dispute identity differs")
            except (TypeError, ValueError, RuntimeError) as exc:
                raise RunStoreError("corrupt_record", "stored task result is invalid") from exc
            return
        if action == "upload_asset":
            if scenario_step.get("worker") != actor.handle:
                raise RunStoreError("corrupt_record", "stored asset actor role differs")
            if set(intent) != {"task_id", "filename", "content_sha256"}:
                raise RunStoreError("corrupt_record", "stored asset intent fields differ")
            if not all(
                isinstance(intent.get(field), str) and bool(intent.get(field))
                for field in ("task_id", "filename", "content_sha256")
            ):
                raise RunStoreError("corrupt_record", "stored asset intent is invalid")
            expected_filename = scenario_step.get("filename", "deliverable.txt")
            expected_content = scenario_step.get("content", "Demo deliverable content")
            if not isinstance(expected_filename, str) or not expected_filename:
                raise RunStoreError("corrupt_record", "scenario asset filename is invalid")
            if not isinstance(expected_content, str):
                raise RunStoreError("corrupt_record", "scenario asset content is invalid")
            expected_content_sha256 = _sha256(expected_content.encode())
            if (
                intent.get("filename") != expected_filename
                or intent.get("content_sha256") != expected_content_sha256
            ):
                raise RunStoreError("corrupt_record", "stored asset intent differs")
            expected_task_id = self._resolve_task_dependency(index, scenario_step, actor.handle)
            if intent.get("task_id") != expected_task_id:
                raise RunStoreError("corrupt_record", "stored asset task dependency differs")
            if state == "completed":
                if result is None or set(result) != {"task_id", "asset"}:
                    raise RunStoreError("corrupt_record", "stored asset result fields differ")
                asset = result.get("asset")
                if not isinstance(asset, Mapping) or set(asset) != {
                    "asset_id",
                    "task_id",
                    "uploader_id",
                    "filename",
                    "content_type",
                    "size_bytes",
                    "content_hash",
                    "uploaded_at",
                }:
                    raise RunStoreError("corrupt_record", "stored asset acknowledgement is invalid")
                if (
                    result.get("task_id") != intent.get("task_id")
                    or asset.get("task_id") != intent.get("task_id")
                    or asset.get("uploader_id") != actor.agent_id
                    or asset.get("filename") != intent.get("filename")
                    or asset.get("content_hash") != intent.get("content_sha256")
                    or not isinstance(asset.get("asset_id"), str)
                    or not asset.get("asset_id")
                ):
                    raise RunStoreError("corrupt_record", "stored asset acknowledgement differs")
            return
        if action == "feedback":
            if scenario_step.get("agent") != actor.handle:
                raise RunStoreError("corrupt_record", "stored feedback actor role differs")
            expected_fields = {"task_id", "to_agent_id", "category", "rating", "comment"}
            if set(intent) != expected_fields or not all(
                isinstance(intent.get(field), str) for field in expected_fields
            ):
                raise RunStoreError("corrupt_record", "stored feedback intent is invalid")
            expected_feedback = {
                "category": scenario_step.get("category"),
                "rating": scenario_step.get("rating"),
                "comment": scenario_step.get("comment", ""),
            }
            if any(not isinstance(value, str) for value in expected_feedback.values()) or any(
                intent.get(field) != value for field, value in expected_feedback.items()
            ):
                raise RunStoreError("corrupt_record", "stored feedback intent differs")
            expected_task_id = self._resolve_task_dependency(index, scenario_step, actor.handle)
            if intent.get("task_id") != expected_task_id:
                raise RunStoreError("corrupt_record", "stored feedback task dependency differs")
            target = scenario_step.get("to_agent_id")
            if isinstance(target, str) and target in self._actors:
                target = self._actors[target].agent_id
            if intent.get("to_agent_id") != target:
                raise RunStoreError("corrupt_record", "stored feedback target differs")
            if state == "completed":
                if result is None or set(result) != {"task_id", "feedback"}:
                    raise RunStoreError("corrupt_record", "stored feedback result fields differ")
                feedback = result.get("feedback")
                if not isinstance(feedback, Mapping) or set(feedback) != {
                    "feedback_id",
                    "task_id",
                    "from_agent_id",
                    "to_agent_id",
                    "category",
                    "rating",
                    "comment",
                    "submitted_at",
                    "visible",
                }:
                    raise RunStoreError(
                        "corrupt_record", "stored feedback acknowledgement is invalid"
                    )
                for field in ("task_id", "to_agent_id", "category", "rating", "comment"):
                    if feedback.get(field) != intent.get(field):
                        raise RunStoreError(
                            "corrupt_record", "stored feedback acknowledgement differs"
                        )
                if (
                    feedback.get("from_agent_id") != actor.agent_id
                    or not isinstance(feedback.get("feedback_id"), str)
                    or not feedback.get("feedback_id")
                ):
                    raise RunStoreError("corrupt_record", "stored feedback acknowledgement differs")
            return
        if action == "submit_rebuttal":
            if scenario_step.get("worker") != actor.handle:
                raise RunStoreError("corrupt_record", "stored rebuttal actor role differs")
            expected_fields = {"task_id", "dispute_id", "rebuttal"}
            if set(intent) != expected_fields or not all(
                isinstance(intent.get(field), str) and bool(intent.get(field))
                for field in expected_fields
            ):
                raise RunStoreError("corrupt_record", "stored rebuttal intent is invalid")
            expected_task_id = self._resolve_task_dependency(index, scenario_step, actor.handle)
            expected_dispute_id = self._resolve_dispute_dependency(index, expected_task_id)
            if (
                intent.get("task_id") != expected_task_id
                or intent.get("dispute_id") != expected_dispute_id
                or intent.get("rebuttal")
                != scenario_step.get("rebuttal", "The deliverable meets the specification.")
            ):
                raise RunStoreError("corrupt_record", "stored rebuttal dependency differs")
            if state == "completed":
                if result is None or set(result) != {"task_id", "dispute_id", "response"}:
                    raise RunStoreError("corrupt_record", "stored rebuttal result fields differ")
                response = result.get("response")
                if (
                    not isinstance(response, Mapping)
                    or result.get("task_id") != intent.get("task_id")
                    or result.get("dispute_id") != intent.get("dispute_id")
                    or response.get("task_id") != intent.get("task_id")
                    or response.get("dispute_id") != intent.get("dispute_id")
                    or response.get("rebuttal") != intent.get("rebuttal")
                    or not isinstance(response.get("rebutted_at"), str)
                ):
                    raise RunStoreError("corrupt_record", "stored rebuttal acknowledgement differs")
            return
        if action == "file_claim":
            if scenario_step.get("poster") != actor.handle:
                raise RunStoreError("corrupt_record", "stored claim actor role differs")
            expected_fields = {
                "task_id",
                "claimant_id",
                "respondent_id",
                "claim",
                "escrow_id",
            }
            if set(intent) != expected_fields or not all(
                isinstance(intent.get(field), str) and bool(intent.get(field))
                for field in expected_fields
            ):
                raise RunStoreError("corrupt_record", "stored claim intent is invalid")
            if intent.get("claimant_id") != actor.agent_id:
                raise RunStoreError("corrupt_record", "stored claim owner differs")
            expected_task_id = self._resolve_task_dependency(index, scenario_step, actor.handle)
            if intent.get("task_id") != expected_task_id:
                raise RunStoreError("corrupt_record", "stored claim task dependency differs")
            expected_claim = scenario_step.get(
                "claim",
                scenario_step.get("reason", "Deliverable does not meet specification"),
            )
            if not isinstance(expected_claim, str) or not expected_claim:
                raise RunStoreError("corrupt_record", "scenario claim is invalid")
            posting_records = [
                record
                for _position, step, record in self._prior_completed(index)
                if step.get("action") == "post_task"
                and self._posting_task_id(record) == expected_task_id
            ]
            assignments = [
                record
                for _position, step, record in self._prior_completed(index)
                if step.get("action") == "accept_bid"
                and record.result is not None
                and record.result.get("task_id") == expected_task_id
            ]
            if len(posting_records) != 1 or len(assignments) > 1:
                raise RunStoreError("dependency_missing", "claim producer facts are incomplete")
            posting_body = cast("Mapping[str, object]", posting_records[0].result)["body"]
            posting_result = (
                posting_body.get("result") if isinstance(posting_body, Mapping) else None
            )
            respondent_id: object = None
            if assignments:
                assignment_response = cast("Mapping[str, object]", assignments[0].result).get(
                    "response"
                )
                assignment_operation = (
                    assignment_response.get("operation")
                    if isinstance(assignment_response, Mapping)
                    else None
                )
                assignment_result = (
                    assignment_operation.get("result")
                    if isinstance(assignment_operation, Mapping)
                    else None
                )
                respondent_id = (
                    assignment_result.get("worker_id")
                    if isinstance(assignment_result, Mapping)
                    else None
                )
            escrow_id = (
                posting_result.get("escrow_id") if isinstance(posting_result, Mapping) else None
            )
            worker_handle = scenario_step.get("worker")
            worker = self._actors.get(cast("str", worker_handle))
            if respondent_id is None and worker_handle is not None and worker is not None:
                respondent_id = worker.agent_id
            if not isinstance(respondent_id, str) or (
                worker_handle is not None and (worker is None or worker.agent_id != respondent_id)
            ):
                raise RunStoreError("corrupt_record", "scenario claim respondent differs")
            if (
                intent.get("claim") != expected_claim
                or intent.get("respondent_id") != respondent_id
                or intent.get("escrow_id") != escrow_id
            ):
                raise RunStoreError("corrupt_record", "stored claim intent differs")
            if state == "completed":
                if result is None or set(result) != {"task_id", "dispute_id", "response"}:
                    raise RunStoreError("corrupt_record", "stored claim result fields differ")
                response = result.get("response")
                if not isinstance(response, Mapping):
                    raise RunStoreError("corrupt_record", "stored claim response is invalid")
                for field in expected_fields:
                    if response.get(field) != intent.get(field):
                        raise RunStoreError("corrupt_record", "stored claim response differs")
                if (
                    result.get("task_id") != intent.get("task_id")
                    or result.get("dispute_id") != response.get("dispute_id")
                    or not isinstance(response.get("dispute_id"), str)
                    or not response.get("dispute_id")
                ):
                    raise RunStoreError("corrupt_record", "stored claim response differs")
            return
        if action == "trigger_ruling":
            context_handle = (
                scenario_step.get("agent")
                or scenario_step.get("poster")
                or scenario_step.get("worker")
            )
            if context_handle != actor.handle:
                raise RunStoreError("corrupt_record", "stored ruling actor role differs")
            expected_fields = {"task_id", "dispute_id"}
            if set(intent) != expected_fields or not all(
                isinstance(intent.get(field), str) and bool(intent.get(field))
                for field in expected_fields
            ):
                raise RunStoreError("corrupt_record", "stored ruling intent is invalid")
            expected_task_id = self._resolve_task_dependency(index, scenario_step, actor.handle)
            expected_dispute_id = self._resolve_dispute_dependency(index, expected_task_id)
            if (
                intent.get("task_id") != expected_task_id
                or intent.get("dispute_id") != expected_dispute_id
            ):
                raise RunStoreError("corrupt_record", "stored ruling dependency differs")
            if state == "completed":
                if result is None or set(result) != {"task_id", "dispute_id", "response"}:
                    raise RunStoreError("corrupt_record", "stored ruling result fields differ")
                response = result.get("response")
                worker_pct = response.get("worker_pct") if isinstance(response, Mapping) else None
                if (
                    not isinstance(response, Mapping)
                    or result.get("task_id") != intent.get("task_id")
                    or result.get("dispute_id") != intent.get("dispute_id")
                    or response.get("task_id") != intent.get("task_id")
                    or response.get("dispute_id") != intent.get("dispute_id")
                    or response.get("status") != "ruled"
                    or type(worker_pct) is not int
                    or not 0 <= worker_pct <= 100
                    or not isinstance(response.get("ruling_summary"), str)
                    or not isinstance(response.get("ruled_at"), str)
                ):
                    raise RunStoreError("corrupt_record", "stored ruling acknowledgement differs")
            return
        if state == "completed":
            raise RunStoreError("corrupt_record", "action has no durable completion validator")

    @staticmethod
    def _posting_state(response: TaskPostingResponse) -> str:
        body: object = response.body
        if response.status_code == 409:
            details = body.get("details") if isinstance(body, Mapping) else None
            body = details.get("publication") if isinstance(details, Mapping) else None
        state = body.get("publication_state") if isinstance(body, Mapping) else None
        if not isinstance(state, str):
            raise RunStoreError("corrupt_record", "posting state is missing")
        return state

    @staticmethod
    def _validate_posting_result(
        intent: TaskPostingIntent, result: Mapping[str, object] | None
    ) -> TaskPostingResponse:
        if result is None or set(result) != {"status_code", "body"}:
            raise RunStoreError("corrupt_record", "completed posting result fields differ")
        status_code = result["status_code"]
        body = result["body"]
        if type(status_code) is not int or not isinstance(body, Mapping):
            raise RunStoreError("corrupt_record", "completed posting result is invalid")
        method = "POST" if status_code == 201 else "GET"
        response = httpx.Response(
            status_code,
            json=dict(body),
            request=httpx.Request(method, "https://durable.invalid/task-publication"),
        )
        try:
            if status_code in {201, 202, 409}:
                parsed = parse_task_posting_submit_response(response, intent)
            else:
                parsed = parse_task_posting_status_response(response, intent.operation_id)
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RunStoreError(
                "corrupt_record", "completed posting acknowledgement is invalid"
            ) from exc
        return parsed

    def _read_step_slot(self, index: int) -> StoredStep | None:
        assert self._steps_directory is not None
        value = self._read_json(self._steps_directory, self._step_name(index))
        if value is None:
            raise RunStoreError("corrupt_record", "step slot is missing")
        current = self._decode_with_fresh_prefix(index, value)
        if current != self._steps[index]:
            raise RunStoreError("corrupt_record", "step slot disagrees with cached state")
        return current

    def step(self, index: int) -> StoredStep | None:
        self._require_open()
        if type(index) is not int or not 0 <= index < len(self._scope.steps):
            raise RunStoreError("step_conflict", "step index is outside the scenario")
        return self._read_step_slot(index)

    def _posting_record(
        self,
        *,
        index: int,
        scenario_step: Mapping[str, object],
        actor: StoredActor,
        intent: TaskPostingIntent,
        state: str,
        revision: int,
    ) -> dict[str, object]:
        hashes = cast("list[str]", self._scope.preparation["step_sha256s"])
        return {
            "schema_version": _SCHEMA_VERSION,
            "run_scope_sha256": self._scope_sha256,
            "index": index,
            "step_sha256": hashes[index],
            "action": scenario_step["action"],
            "actor_handle": actor.handle,
            "agent_id": actor.agent_id,
            "actor_public_fingerprint_sha256": actor.public_fingerprint_sha256,
            "intent": intent.as_json(),
            "revision": revision,
            "state": state,
            "result": None,
            "diagnostic": None,
        }

    def _stored_step_record(
        self,
        current: StoredStep,
        *,
        state: str,
        revision: int,
        result: Mapping[str, object] | None,
        diagnostic: str | None,
    ) -> dict[str, object]:
        hashes = cast("list[str]", self._scope.preparation["step_sha256s"])
        intent = (
            current.intent.as_json()
            if isinstance(current.intent, TaskPostingIntent)
            else dict(current.intent)
        )
        return {
            "schema_version": _SCHEMA_VERSION,
            "run_scope_sha256": self._scope_sha256,
            "index": current.index,
            "step_sha256": hashes[current.index],
            "action": self._scope.steps[current.index]["action"],
            "actor_handle": current.actor_handle,
            "agent_id": current.agent_id,
            "actor_public_fingerprint_sha256": current.actor_public_fingerprint_sha256,
            "intent": intent,
            "revision": revision,
            "state": state,
            "result": None if result is None else dict(result),
            "diagnostic": diagnostic,
        }

    def reserve_step(
        self,
        *,
        index: int,
        step: Mapping[str, object],
        actor_handle: str,
        intent: Mapping[str, object],
    ) -> StoredStep:
        """Persist one non-posting action intent before any transport."""
        self._require_open()
        if type(index) is not int or not 0 <= index < len(self._scope.steps):
            raise RunStoreError("step_conflict", "step index is outside the scenario")
        scenario_step = self._scope.steps[index]
        if _canonical_bytes(step, reason="step_conflict") != _canonical_bytes(
            scenario_step, reason="step_conflict"
        ):
            raise RunStoreError("step_conflict", "selected step differs from frozen scenario")
        if scenario_step.get("action") == "post_task":
            raise RunStoreError("intent_conflict", "posting requires a typed posting intent")
        actor = self._read_actor_slot(actor_handle)
        if actor.agent_id is None:
            raise RunStoreError("registration_missing", "actor has no durable registration")
        _canonical_bytes(intent, reason="intent_conflict")
        existing = self._read_step_slot(index)
        if existing is not None:
            if (
                existing.actor_handle != actor_handle
                or not isinstance(existing.intent, Mapping)
                or isinstance(existing.intent, TaskPostingIntent)
                or not _strict_json_equal(existing.intent, intent)
            ):
                raise RunStoreError("intent_conflict", "durable step reservation differs")
            return existing
        current = StoredStep(
            index=index,
            state="prepared",
            actor_handle=actor.handle,
            agent_id=actor.agent_id,
            actor_public_fingerprint_sha256=actor.public_fingerprint_sha256,
            intent=_freeze_mapping(dict(intent)),
            revision=1,
        )
        record = self._stored_step_record(
            current,
            state="prepared",
            revision=1,
            result=None,
            diagnostic=None,
        )
        stored = self._replace_step(index, record)
        if stored is None:
            raise RunStoreError("corrupt_record", "step reservation readback differs")
        self._steps[index] = stored
        return stored

    def _replace_step(self, index: int, record: Mapping[str, object]) -> StoredStep | None:
        validated = self._decode_with_fresh_prefix(index, record)
        if validated is None:
            raise RunStoreError("corrupt_record", "replacement step cannot be empty")
        content = _canonical_bytes(record, reason="storage_failure")
        if len(content) > self._max_record_bytes:
            raise RunStoreError("record_too_large", "step record exceeds configured limit")
        assert self._steps_directory is not None
        name = self._step_name(index)
        try:
            replace_record(self._steps_directory, name, content)
        except RunStoreFileError as exc:
            raise self._translate(exc) from exc
        readback = self._decode_with_fresh_prefix(
            index, self._read_json(self._steps_directory, name)
        )
        if readback != validated:
            raise RunStoreError("corrupt_record", "replacement step readback differs")
        return readback

    def reserve_posting(
        self,
        *,
        index: int,
        step: Mapping[str, object],
        actor_handle: str,
        intent: TaskPostingIntent,
    ) -> StoredStep:
        self._require_open()
        if type(index) is not int or not 0 <= index < len(self._scope.steps):
            raise RunStoreError("step_conflict", "step index is outside the scenario")
        scenario_step = self._scope.steps[index]
        if _canonical_bytes(step, reason="step_conflict") != _canonical_bytes(
            scenario_step, reason="step_conflict"
        ):
            raise RunStoreError("step_conflict", "selected step differs from frozen scenario")
        actor = self._read_actor_slot(actor_handle)
        if actor.agent_id is None:
            raise RunStoreError("registration_missing", "actor has no durable registration")
        existing = self._read_step_slot(index)
        expected_terms = {
            "title": scenario_step.get("title"),
            "spec": scenario_step.get("spec"),
            "reward": scenario_step.get("reward"),
            "bidding_deadline_seconds": scenario_step.get("bidding_deadline_seconds"),
            "acceptance_window_seconds": scenario_step.get("acceptance_window_seconds"),
            "execution_deadline_seconds": scenario_step.get("execution_deadline_seconds"),
            "review_deadline_seconds": scenario_step.get("review_deadline_seconds"),
        }
        actual_terms = {field: getattr(intent, field) for field in expected_terms}
        if (
            scenario_step.get("action") != "post_task"
            or scenario_step.get("poster") != actor_handle
            or intent.actor_id != actor.agent_id
            or actual_terms != expected_terms
        ):
            raise RunStoreError(
                "intent_conflict", "posting intent differs from frozen ownership or terms"
            )
        if existing is not None:
            if (
                existing.actor_handle != actor.handle
                or existing.agent_id != actor.agent_id
                or existing.actor_public_fingerprint_sha256 != actor.public_fingerprint_sha256
                or existing.intent != intent
            ):
                raise RunStoreError("intent_conflict", "durable posting reservation differs")
            return existing
        expected_record = self._posting_record(
            index=index,
            scenario_step=scenario_step,
            actor=actor,
            intent=intent,
            state="prepared",
            revision=1,
        )
        assert self._steps_directory is not None
        name = self._step_name(index)
        content = _canonical_bytes(expected_record, reason="storage_failure")
        if len(content) > self._max_record_bytes:
            raise RunStoreError("record_too_large", "posting record exceeds configured limit")
        try:
            replace_record(self._steps_directory, name, content)
        except RunStoreFileError as exc:
            raise self._translate(exc) from exc
        readback = self._read_json(self._steps_directory, name)
        stored = self._decode_step(readback)
        if stored is None or stored.intent != intent:
            raise RunStoreError("corrupt_record", "posting reservation readback differs")
        self._steps[index] = stored
        return stored

    def mark_dispatching(self, *, index: int, expected_revision: int) -> StoredStep:
        self._require_open()
        if type(expected_revision) is not int:
            raise RunStoreError("stale_revision", "posting revision must be a strict integer")
        if type(index) is not int or not 0 <= index < len(self._scope.steps):
            raise RunStoreError("stale_revision", "posting reservation does not exist")
        current = self._read_step_slot(index)
        if current is None:
            raise RunStoreError("stale_revision", "posting reservation does not exist")
        self._read_actor_slot(current.actor_handle)
        if current.state == "dispatching" and expected_revision == current.revision:
            return current
        if current.state != "prepared" or expected_revision != current.revision:
            raise RunStoreError("stale_revision", "posting revision is stale")
        record = self._stored_step_record(
            current,
            state="dispatching",
            revision=current.revision + 1,
            result=None,
            diagnostic=None,
        )
        dispatched = self._replace_step(index, record)
        if dispatched is None or dispatched.revision != current.revision + 1:
            raise RunStoreError("corrupt_record", "dispatch readback differs")
        self._steps[index] = dispatched
        return dispatched

    def finish_step(
        self,
        *,
        index: int,
        expected_revision: int,
        state: str,
        result: Mapping[str, object],
        diagnostic: str | None,
    ) -> StoredStep:
        """Persist and read back one terminal acknowledgement."""
        self._require_open()
        if state not in {"completed", "definitive_rejection", "attention_required"}:
            raise RunStoreError("step_conflict", "terminal step state is invalid")
        if diagnostic is not None and len(diagnostic) > self._diagnostic_limit:
            diagnostic = diagnostic[: self._diagnostic_limit]
        current = self._read_step_slot(index)
        if current is None:
            raise RunStoreError("stale_revision", "step reservation does not exist")
        if current.state in {"completed", "definitive_rejection", "attention_required"}:
            if (
                expected_revision == current.revision
                and current.state == state
                and current.result is not None
                and _strict_json_equal(current.result, result)
            ):
                return current
            raise RunStoreError("stale_revision", "terminal step cannot be changed")
        if current.state != "dispatching" or current.revision != expected_revision:
            raise RunStoreError("stale_revision", "step revision is stale")
        record = self._stored_step_record(
            current,
            state=state,
            revision=current.revision + 1,
            result=result,
            diagnostic=diagnostic,
        )
        finished = self._replace_step(index, record)
        if finished is None or finished.state != state:
            raise RunStoreError("corrupt_record", "terminal step readback differs")
        self._steps[index] = finished
        return finished

    def complete_posting(
        self,
        *,
        index: int,
        expected_revision: int,
        response: TaskPostingResponse,
    ) -> StoredStep:
        current = self._read_step_slot(index)
        if current is None or not isinstance(current.intent, TaskPostingIntent):
            raise RunStoreError("step_conflict", "posting reservation does not exist")
        result: dict[str, object] = {
            "status_code": response.status_code,
            "body": dict(response.body),
        }
        parsed = self._validate_posting_result(current.intent, result)
        publication_state = self._posting_state(parsed)
        if publication_state == "awaiting_lock":
            return current
        if publication_state == "blocked_integrity":
            return self.finish_step(
                index=index,
                expected_revision=expected_revision,
                state="attention_required",
                result=result,
                diagnostic="blocked_integrity",
            )
        if publication_state != "published":
            raise RunStoreError("corrupt_record", "posting acknowledgement state is invalid")
        return self.finish_step(
            index=index,
            expected_revision=expected_revision,
            state="completed",
            result=result,
            diagnostic=None,
        )

    @property
    def completed(self) -> bool:
        self._require_open()
        for index, scenario_step in enumerate(self._scope.steps):
            current = self._read_step_slot(index)
            if scenario_step.get("action") == "register":
                handle = scenario_step.get("agent")
                actor = self._actors.get(cast("str", handle))
                if actor is None or actor.agent_id is None:
                    return False
                continue
            if current is None or current.state != "completed":
                return False
        return True


__all__ = [
    "ActorDefinition",
    "RunEnvironment",
    "RunStore",
    "RunStoreError",
    "StoredActor",
    "StoredStep",
]
