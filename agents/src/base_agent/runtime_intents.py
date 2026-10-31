"""Caller-owned durable journal for native Task Board mutation intents."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from service_commons.maintenance_lock import (
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_maintenance_lock,
)

from base_agent.runtime_intent_ack import (
    classify_acknowledgment,
    validate_owner_intent,
    validate_stored_intent,
)
from base_agent.runtime_intent_files import (
    SafeDirectory,
    decode_record,
    open_private_child,
    open_root_directory,
    publish_owner_exclusive,
    publish_record_exclusive,
    read_record_bytes,
    remove_private_staging_record,
    replace_record,
    validate_absolute_path,
    validate_record_admission,
)
from base_agent.runtime_intent_models import (
    FrozenTaskIntent,
    NewTaskIntent,
    RuntimeIntentError,
    RuntimeOwner,
    canonical_json_bytes,
    canonical_sha256,
    intent_from_json,
    owner_from_json,
    require_integer,
    require_task_id,
)
from base_agent.runtime_jobs import JOB_DIRECTORY, reconcile_job_directory

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from pathlib import Path
    from types import TracebackType

    from service_commons.maintenance_lock import HeldMaintenanceLock


__all__ = [
    "FileTaskIntentStore",
    "FrozenTaskIntent",
    "NewTaskIntent",
    "RuntimeIntentError",
    "RuntimeOwner",
]


@dataclass(slots=True)
class PostingNamespace:
    """Owned posting descriptors borrowing an open intent-store lease."""

    parent: FileTaskIntentStore
    identity: SafeDirectory
    postings: SafeDirectory
    records: SafeDirectory
    _closed: bool = False

    def verify(self) -> None:
        if self._closed:
            raise RuntimeIntentError("invalid_input", "posting namespace is closed")
        identity, _intents = self.parent._require_open()
        if (identity.device, identity.inode) != (
            self.identity.device,
            self.identity.inode,
        ) or identity.path != self.identity.path:
            raise RuntimeIntentError("unsafe_path", "posting parent identity changed")
        self.identity.verify()
        self.postings.verify()
        self.records.verify()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        error: BaseException | None = None
        for directory in (self.records, self.postings):
            try:
                directory.close()
            except BaseException as exc:
                if error is None:
                    error = exc
        if error is not None:
            raise error


class FileTaskIntentStore:
    """Private atomic JSON store held under one process-long identity lease."""

    def __init__(
        self,
        *,
        root_directory: Path,
        owner: object,
        diagnostic_limit: int,
        max_record_bytes: int,
    ) -> None:
        validate_absolute_path(root_directory)
        if not isinstance(owner, RuntimeOwner):
            raise RuntimeIntentError("invalid_input", "owner must be a RuntimeOwner")
        self.root_directory = root_directory
        self.owner = owner
        self._owner_sha256 = canonical_sha256(owner.as_json())
        self.diagnostic_limit = require_integer(diagnostic_limit, "diagnostic_limit", minimum=1)
        self.max_record_bytes = require_integer(max_record_bytes, "max_record_bytes", minimum=1)
        self.identity_directory = root_directory / "identities" / owner.key_fingerprint
        self.owner_path = self.identity_directory / "owner.json"
        self.lock_path = self.identity_directory / "runtime.lock"
        self._intents_path = self.identity_directory / "intents"
        self._root: SafeDirectory | None = None
        self._identities: SafeDirectory | None = None
        self._identity: SafeDirectory | None = None
        self._intents: SafeDirectory | None = None
        self._lease_context: AbstractContextManager[HeldMaintenanceLock] | None = None
        self._held_lease: HeldMaintenanceLock | None = None
        self._observed_revisions: dict[tuple[str, str], int] = {}

    def __enter__(self) -> Self:
        if self._lease_context is not None:
            raise RuntimeIntentError("invalid_input", "intent store is already open")
        try:
            self._root = open_root_directory(self.root_directory)
            self._identities = open_private_child(
                self._root, "identities", enforce_private_mode=False
            )
            self._identity = open_private_child(self._identities, self.owner.key_fingerprint)
            self._intents = open_private_child(self._identity, "intents")
            lease = hold_maintenance_lock(
                self.lock_path,
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
            )
            self._lease_context = lease
            self._held_lease = lease.__enter__()
            self._reconcile_identity_directory()
            self._validate_or_publish_owner()
            self._reconcile_staging_records()
            self._all()
            return self
        except MaintenanceLockError as exc:
            self._release(None, None, None)
            reason = "lease_busy" if exc.reason == "busy" else exc.reason
            if reason not in {"unsafe_path", "unsafe_mode", "lease_busy"}:
                reason = "storage_failure"
            raise RuntimeIntentError(reason, "identity lease could not be acquired") from exc
        except BaseException:
            self._release(None, None, None)
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release(exc_type, exc, traceback)

    def _release(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        lease = self._lease_context
        self._lease_context = None
        self._held_lease = None
        release_error: BaseException | None = None
        if lease is not None:
            try:
                lease.__exit__(exc_type, exc, traceback)
            except BaseException as error:
                release_error = error
        for attribute in ("_intents", "_identity", "_identities", "_root"):
            directory = getattr(self, attribute)
            setattr(self, attribute, None)
            if directory is not None:
                try:
                    directory.close()
                except BaseException as error:
                    if release_error is None:
                        release_error = error
        if release_error is not None and exc is None:
            raise release_error

    def _require_open(self) -> tuple[SafeDirectory, SafeDirectory]:
        if (
            self._identity is None
            or self._intents is None
            or self._lease_context is None
            or self._held_lease is None
        ):
            raise RuntimeIntentError("invalid_input", "intent store is not open")
        try:
            self._held_lease.verify_identity()
        except MaintenanceLockError as exc:
            reason = (
                exc.reason if exc.reason in {"unsafe_path", "unsafe_mode"} else "storage_failure"
            )
            raise RuntimeIntentError(reason, "identity lease changed while held") from exc
        self._identity.verify()
        self._intents.verify()
        return self._identity, self._intents

    def _validate_or_publish_owner(self) -> None:
        identity, _intents = self._require_open()
        expected = canonical_json_bytes(self.owner.as_json())
        if len(expected) > self.max_record_bytes:
            raise RuntimeIntentError("record_too_large", "owner record exceeds configured limit")
        current = read_record_bytes(identity, "owner.json", max_record_bytes=self.max_record_bytes)
        if current is None:
            published = publish_owner_exclusive(identity, "owner.json", expected)
            if published:
                current = read_record_bytes(
                    identity, "owner.json", max_record_bytes=self.max_record_bytes
                )
            else:
                current = read_record_bytes(
                    identity, "owner.json", max_record_bytes=self.max_record_bytes
                )
        if current is None:
            raise RuntimeIntentError("storage_failure", "owner record was not published")
        observed = owner_from_json(decode_record(current, name="owner.json"))
        if observed != self.owner:
            raise RuntimeIntentError("owner_conflict", "journal owner does not match")
        if current != expected:
            raise RuntimeIntentError("corrupt_record", "owner record is not canonical JSON")

    def _reconcile_identity_directory(self) -> None:
        identity, intents = self._require_open()
        try:
            with os.scandir(identity.descriptor) as entries:
                names = sorted(entry.name for entry in entries)
        except OSError as exc:
            raise RuntimeIntentError("unsafe_path", "identity directory cannot be scanned") from exc
        staging = re.compile(r"^\.owner\.json\.[0-9a-f]{24}\.tmp$")
        allowed = {"owner.json", "runtime.lock", "intents", "postings", JOB_DIRECTORY}
        for name in names:
            if staging.fullmatch(name) is not None:
                remove_private_staging_record(identity, name)
            elif name not in allowed:
                raise RuntimeIntentError(
                    "corrupt_record", "identity directory contains an unknown entry"
                )
            elif name in {"owner.json", "runtime.lock"}:
                validate_record_admission(identity, name)
            elif name == "postings":
                postings = open_private_child(identity, "postings")
                postings.close()
            elif name == JOB_DIRECTORY:
                reconcile_job_directory(identity)
        intents.verify()

    def open_posting_namespace(self) -> PostingNamespace:
        """Open the posting namespace under this store's already-held lease."""

        identity, _intents = self._require_open()
        postings: SafeDirectory | None = None
        records: SafeDirectory | None = None
        try:
            postings = open_private_child(identity, "postings")
            records = open_private_child(postings, "records")
            namespace = PostingNamespace(self, identity, postings, records)
            namespace.verify()
            return namespace
        except BaseException:
            if records is not None:
                records.close()
            if postings is not None:
                postings.close()
            raise

    def open_job_directory(self) -> SafeDirectory:
        """Open the worker job directory under this store's already-held lease."""
        identity, _intents = self._require_open()
        return open_private_child(identity, JOB_DIRECTORY)

    def intent_path(self, task_id: str, slot: str) -> Path:
        task_id = require_task_id(task_id)
        if slot not in {"bid", "acceptance", "submission", "review-decision"}:
            raise RuntimeIntentError("invalid_input", "slot is unsupported")
        digest = hashlib.sha256(
            canonical_json_bytes({"slot": slot, "task_id": task_id})
        ).hexdigest()
        return self._intents_path / f"{digest}.json"

    def _record_name(self, task_id: str, slot: str) -> str:
        return self.intent_path(task_id, slot).name

    def _load_name(self, name: str) -> FrozenTaskIntent | None:
        _identity, intents = self._require_open()
        content = read_record_bytes(intents, name, max_record_bytes=self.max_record_bytes)
        if content is None:
            return None
        value = intent_from_json(decode_record(content, name=name))
        if value.owner_sha256 != self._owner_sha256:
            raise RuntimeIntentError("corrupt_record", "intent belongs to another owner")
        if canonical_json_bytes(value.as_json()) != content:
            raise RuntimeIntentError("corrupt_record", "intent record is not canonical JSON")
        try:
            validate_stored_intent(self.owner, value)
        except RuntimeIntentError as exc:
            if exc.reason == "corrupt_record":
                raise
            raise RuntimeIntentError(
                "corrupt_record", "stored intent violates its owner-bound contract"
            ) from exc
        key = (value.task_id, value.slot)
        observed = self._observed_revisions.get(key)
        if observed is not None and value.revision < observed:
            raise RuntimeIntentError("stale_revision", "an older intent revision was observed")
        self._observed_revisions[key] = value.revision
        return value

    def load(self, task_id: str, slot: str) -> FrozenTaskIntent | None:
        name = self._record_name(task_id, slot)
        value = self._load_name(name)
        if value is not None and (value.task_id != task_id or value.slot != slot):
            raise RuntimeIntentError("corrupt_record", "hashed intent path has wrong identity")
        return value

    def _all(self) -> tuple[FrozenTaskIntent, ...]:
        names = self._journal_names()
        values: list[FrozenTaskIntent] = []
        identities: set[tuple[str, str]] = set()
        operations: dict[str, tuple[str, str]] = {}
        for name in names:
            value = self._load_name(name)
            if value is None:
                raise RuntimeIntentError("corrupt_record", "intent disappeared during scan")
            expected_name = self._record_name(value.task_id, value.slot)
            identity = (value.task_id, value.slot)
            if expected_name != name or identity in identities:
                raise RuntimeIntentError("corrupt_record", "intent filename or identity conflicts")
            previous_identity = operations.get(value.operation_id)
            if previous_identity is not None and previous_identity != identity:
                raise RuntimeIntentError(
                    "operation_conflict", "operation ID belongs to multiple stored intents"
                )
            identities.add(identity)
            operations[value.operation_id] = identity
            values.append(value)
        return tuple(values)

    def _reconcile_staging_records(self) -> None:
        _identity, intents = self._require_open()
        try:
            with os.scandir(intents.descriptor) as entries:
                names = sorted(entry.name for entry in entries)
        except OSError as exc:
            raise RuntimeIntentError("unsafe_path", "intent directory cannot be scanned") from exc
        staging = re.compile(r"^\.[0-9a-f]{64}\.json\.[0-9a-f]{24}\.tmp$")
        for name in names:
            if staging.fullmatch(name) is not None:
                remove_private_staging_record(intents, name)
            elif name.endswith(".json"):
                validate_record_admission(intents, name)

    def _journal_names(self) -> list[str]:
        _identity, intents = self._require_open()
        try:
            with os.scandir(intents.descriptor) as entries:
                names = sorted(entry.name for entry in entries)
        except OSError as exc:
            raise RuntimeIntentError("unsafe_path", "intent directory cannot be scanned") from exc
        for name in names:
            stem = name[:-5] if name.endswith(".json") else ""
            if len(stem) != 64 or any(character not in "0123456789abcdef" for character in stem):
                raise RuntimeIntentError(
                    "corrupt_record", "intent directory contains an unknown entry"
                )
        return names

    def _assert_operation_available(self, intent: NewTaskIntent) -> None:
        for stored in self._all():
            if stored.operation_id == intent.operation_id and (
                stored.task_id != intent.task_id or stored.slot != intent.slot
            ):
                raise RuntimeIntentError(
                    "operation_conflict", "operation ID already belongs to another slot"
                )

    def _write(self, value: FrozenTaskIntent, *, initial: bool) -> FrozenTaskIntent:
        _identity, intents = self._require_open()
        content = canonical_json_bytes(value.as_json())
        if len(content) > self.max_record_bytes:
            raise RuntimeIntentError("record_too_large", "intent record exceeds configured limit")
        name = self._record_name(value.task_id, value.slot)
        if initial:
            published = publish_record_exclusive(intents, name, content)
            if not published:
                winner = self._load_name(name)
                if winner is None:
                    raise RuntimeIntentError(
                        "storage_failure", "concurrent intent winner disappeared"
                    )
                if winner.semantic_sha256 == value.semantic_sha256:
                    return winner
                raise RuntimeIntentError(
                    "slot_conflict", "concurrent intent winner has different terms"
                )
        else:
            replace_record(intents, name, content)
        persisted = self._load_name(name)
        if persisted != value:
            raise RuntimeIntentError("storage_failure", "persisted intent did not validate")
        assert persisted is not None
        return persisted

    def create(self, intent: object) -> FrozenTaskIntent:
        if not isinstance(intent, NewTaskIntent):
            raise RuntimeIntentError("invalid_input", "intent must be a NewTaskIntent")
        current = self.load(intent.task_id, intent.slot)
        candidate = FrozenTaskIntent.pending(intent, owner_sha256=self._owner_sha256)
        if current is not None:
            if current.semantic_sha256 == candidate.semantic_sha256:
                return current
            raise RuntimeIntentError("slot_conflict", "slot already contains different terms")
        validate_owner_intent(self.owner, intent)
        self._assert_operation_available(intent)
        return self._write(candidate, initial=True)

    def load_or_create(
        self,
        task_id: str,
        slot: str,
        factory: Callable[[], NewTaskIntent],
    ) -> FrozenTaskIntent:
        current = self.load(task_id, slot)
        if current is not None:
            return current
        intent = factory()
        if intent.task_id != task_id or intent.slot != slot:
            raise RuntimeIntentError("invalid_input", "intent factory returned another slot")
        return self.create(intent)

    def unfinished(self) -> tuple[FrozenTaskIntent, ...]:
        values = [
            value
            for value in self._all()
            if value.state in {"pending", "blocked", "attention_required"}
        ]
        return tuple(sorted(values, key=lambda item: (item.task_id, item.slot)))

    def record_acknowledgment(
        self,
        *,
        task_id: str,
        slot: str,
        expected_revision: int,
        status_code: int,
        body: object,
    ) -> FrozenTaskIntent:
        expected_revision = require_integer(expected_revision, "expected_revision", minimum=1)
        current = self.load(task_id, slot)
        if current is None:
            raise RuntimeIntentError("invalid_input", "intent slot does not exist")
        if current.revision != expected_revision:
            raise RuntimeIntentError("stale_revision", "intent revision is no longer current")
        if current.state in {"completed", "definitive_rejection"}:
            return current
        observation = classify_acknowledgment(current, status_code=status_code, body=body)
        updated = current.with_observation(
            state=observation.state,
            result=observation.result,
            diagnostic=observation.diagnostic,
            diagnostic_limit=self.diagnostic_limit,
        )
        return self._write(updated, initial=False)
