"""Durable feeder task-posting journal under the runtime identity lease."""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self, cast

from service_auth.posting_journal import (
    PostingJournalError,
    RawTask,
    TaskSourceSnapshot,
    ValidatedPostingRecord,
    audit_posting_ledger,
    canonical_json_bytes,
    canonical_sha256,
    decode_canonical,
    new_head,
    normalize_observation,
    ordered_entries,
    parse_head,
    parse_source_bytes,
    source_matches_binding,
    validate_record,
    validate_source_binding,
)
from service_auth.task_postings import TaskPostingIntent

from base_agent.runtime_intent_files import (
    publish_record_exclusive,
    read_record_bytes,
    remove_private_staging_record,
    replace_record,
    validate_record_admission,
)
from base_agent.runtime_intent_models import RuntimeIntentError, require_integer

if TYPE_CHECKING:
    from types import TracebackType

    from base_agent.runtime_intent_models import RuntimeOwner
    from base_agent.runtime_intents import FileTaskIntentStore, PostingNamespace

_RECORD_NAME = re.compile(r"^[0-9]{20}\.json$")
_POSTING_STAGING = re.compile(r"^\.(?:source\.jsonl|head\.json)\.[0-9a-f]{24}\.tmp$")
_RECORD_STAGING = re.compile(r"^\.[0-9]{20}\.json\.[0-9a-f]{24}\.tmp$")
_UNRESOLVED = frozenset({"prepared", "awaiting_lock", "attention_required", "blocked_integrity"})


def _translate(exc: PostingJournalError, *, persisted: bool = False) -> RuntimeIntentError:
    reason = exc.reason
    if persisted and reason not in {"record_too_large", "owner_conflict"}:
        reason = "corrupt_record"
    return RuntimeIntentError(reason, str(exc))


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class FrozenPostingRecord:
    """One immutable, fully validated posting record."""

    _json: Mapping[str, object]
    intent: TaskPostingIntent
    raw_task: Mapping[str, object]

    @classmethod
    def from_validated(cls, value: ValidatedPostingRecord) -> FrozenPostingRecord:
        rendered = value.as_json()
        return cls(
            _json=cast("Mapping[str, object]", _freeze(rendered)),
            intent=value.intent,
            raw_task=cast("Mapping[str, object]", _freeze(value.raw_task.as_json())),
        )

    def __getattr__(self, name: str) -> object:
        if name in self._json:
            return self._json[name]
        raise AttributeError(name)

    def as_json(self) -> dict[str, object]:
        return copy.deepcopy(cast("dict[str, object]", _thaw(self._json)))


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class FileTaskPostingStore:
    """Private posting store borrowing a parent intent store's exclusive lease."""

    def __init__(self, parent: FileTaskIntentStore, *, max_source_bytes: int) -> None:
        self._parent = parent
        self._max_source_bytes = require_integer(max_source_bytes, "max_source_bytes", minimum=1)
        self._namespace: PostingNamespace | None = None

    @classmethod
    def attach(cls, parent: FileTaskIntentStore, *, max_source_bytes: int) -> FileTaskPostingStore:
        return cls(parent, max_source_bytes=max_source_bytes)

    @property
    def owner(self) -> RuntimeOwner:
        """Expose the immutable owner only while the borrowed lease is valid."""
        self._require_open()
        return self._parent.owner

    def __enter__(self) -> Self:
        if self._namespace is not None:
            raise RuntimeIntentError("invalid_input", "posting store is already open")
        namespace: PostingNamespace | None = None
        try:
            namespace = self._parent.open_posting_namespace()
            self._namespace = namespace
            self._reconcile_entries()
            state = self._audit()
            if state == "adoption_required":
                self._adopt_successor()
                if self._audit() != "ready":
                    raise RuntimeIntentError(
                        "corrupt_record", "successor adoption did not validate"
                    )
            return self
        except BaseException:
            self._namespace = None
            if namespace is not None:
                namespace.close()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, traceback
        namespace = self._namespace
        self._namespace = None
        if namespace is not None:
            try:
                namespace.close()
            except BaseException:
                if exc is None:
                    raise

    def _require_open(self) -> PostingNamespace:
        namespace = self._namespace
        if namespace is None:
            raise RuntimeIntentError("invalid_input", "posting store is not open")
        namespace.verify()
        return namespace

    def _scan(self, directory: Any, *, records: bool) -> list[str]:
        directory.verify()
        try:
            with os.scandir(directory.descriptor) as entries:
                names = sorted(entry.name for entry in entries)
        except OSError as exc:
            raise RuntimeIntentError("unsafe_path", "posting directory cannot be scanned") from exc
        expected = _RECORD_NAME if records else None
        for name in names:
            if (records and _RECORD_STAGING.fullmatch(name)) or (
                not records and _POSTING_STAGING.fullmatch(name)
            ):
                remove_private_staging_record(directory, name)
            elif records and expected is not None and expected.fullmatch(name):
                validate_record_admission(directory, name)
            elif not records and name in {"source.jsonl", "head.json", "records"}:
                if name != "records":
                    validate_record_admission(directory, name)
            else:
                raise RuntimeIntentError(
                    "corrupt_record", "posting directory contains an unknown entry"
                )
        return [name for name in names if not name.startswith(".")]

    def _reconcile_entries(self) -> None:
        namespace = self._require_open()
        self._scan(namespace.postings, records=False)
        self._scan(namespace.records, records=True)

    def _read_source(self) -> bytes | None:
        namespace = self._require_open()
        try:
            return read_record_bytes(
                namespace.postings,
                "source.jsonl",
                max_record_bytes=self._max_source_bytes,
            )
        except RuntimeIntentError as exc:
            if exc.reason == "corrupt_record":
                raise RuntimeIntentError(
                    "record_too_large", "source snapshot exceeds configured limit"
                ) from exc
            raise

    def _read_head_bytes(self) -> bytes | None:
        namespace = self._require_open()
        return read_record_bytes(
            namespace.postings,
            "head.json",
            max_record_bytes=self._parent.max_record_bytes,
        )

    def _record_byte_map(self) -> dict[str, bytes]:
        namespace = self._require_open()
        names = self._scan(namespace.records, records=True)
        result: dict[str, bytes] = {}
        for name in names:
            content = read_record_bytes(
                namespace.records, name, max_record_bytes=self._parent.max_record_bytes
            )
            if content is None:
                raise RuntimeIntentError("corrupt_record", "posting record disappeared")
            result[name] = content
        return result

    def _audit(self) -> str:
        self._require_open()
        try:
            audit = audit_posting_ledger(
                owner_json=self._parent.owner.as_json(),
                source_bytes=self._read_source(),
                head_bytes=self._read_head_bytes(),
                record_bytes=self._record_byte_map(),
                max_source_bytes=self._max_source_bytes,
                max_record_bytes=self._parent.max_record_bytes,
                diagnostic_limit=self._parent.diagnostic_limit,
            )
        except PostingJournalError as exc:
            raise _translate(exc, persisted=True) from exc
        if audit.state == "invalid":
            finding = audit.findings[0]
            raise RuntimeIntentError(
                finding.reason, f"invalid posting artifact: {finding.identity}"
            )
        return audit.state

    def _head_value(self) -> dict[str, object] | None:
        content = self._read_head_bytes()
        if content is None:
            return None
        try:
            return parse_head(decode_canonical(content, identity="head.json"))
        except PostingJournalError as exc:
            raise _translate(exc, persisted=True) from exc

    def _snapshot(self, head: Mapping[str, object]) -> TaskSourceSnapshot:
        content = self._read_source()
        if content is None:
            raise RuntimeIntentError("corrupt_record", "bound source snapshot is missing")
        binding = cast("Mapping[str, object]", head["source_binding"])
        try:
            snapshot = parse_source_bytes(
                content,
                source_path=Path(cast("str", binding["source_path"])),
                max_bytes=self._max_source_bytes,
            )
        except PostingJournalError as exc:
            raise _translate(exc, persisted=True) from exc
        if not source_matches_binding(snapshot, binding, allow_relocation=False):
            raise RuntimeIntentError("corrupt_record", "bound source snapshot differs from head")
        return snapshot

    def _validated_records(
        self, head: Mapping[str, object], snapshot: TaskSourceSnapshot
    ) -> tuple[FrozenPostingRecord, ...]:
        byte_map = self._record_byte_map()
        previous = canonical_sha256(
            {
                "schema_version": 1,
                "owner_sha256": head["owner_sha256"],
                "source_binding_sha256": head["source_binding_sha256"],
            }
        )
        values: list[FrozenPostingRecord] = []
        for sequence, name in enumerate(sorted(byte_map)):
            try:
                parsed = validate_record(
                    decode_canonical(byte_map[name], identity=name),
                    snapshot=snapshot,
                    head=head,
                    expected_sequence=sequence,
                    previous_chain_sha256=previous,
                    diagnostic_limit=self._parent.diagnostic_limit,
                )
            except PostingJournalError as exc:
                raise _translate(exc, persisted=True) from exc
            if parsed.intent.actor_id != self._parent.owner.registered_agent_id:
                raise RuntimeIntentError("corrupt_record", "posting actor differs from owner")
            frozen = FrozenPostingRecord.from_validated(parsed)
            values.append(frozen)
            previous = cast("str", parsed.value["selection_chain_sha256"])
        return tuple(values)

    def _adopt_successor(self) -> None:
        head = self._head_value()
        if head is None:
            raise RuntimeIntentError("corrupt_record", "successor has no head")
        records = self._validated_records(head, self._snapshot(head))
        count = cast("int", head["committed_selection_count"])
        if len(records) != count + 1:
            raise RuntimeIntentError("corrupt_record", "posting successor is not unique")
        successor = records[-1]
        advanced = copy.deepcopy(head)
        advanced.update(
            {
                "revision": count + 1,
                "committed_selection_count": count + 1,
                "last_selection_id": successor.selection_id,
                "last_record_semantic_sha256": successor.semantic_sha256,
                "chain_sha256": successor.selection_chain_sha256,
            }
        )
        self._replace_head(advanced)

    def _replace_head(self, head: Mapping[str, object]) -> None:
        namespace = self._require_open()
        content = canonical_json_bytes(head)
        if len(content) > self._parent.max_record_bytes:
            raise RuntimeIntentError("record_too_large", "posting head exceeds configured limit")
        replace_record(namespace.postings, "head.json", content)

    def head(self) -> dict[str, object] | None:
        state = self._audit()
        if state in {"empty", "unbound_source"}:
            return None
        return self._head_value()

    def bind_source(  # noqa: PLR0912
        self, binding_json: object, *, source_bytes: bytes
    ) -> dict[str, object]:
        self._require_open()
        state = self._audit()
        current_head = self._head_value()
        existing_source = self._read_source()
        if current_head is None and existing_source is not None and existing_source != source_bytes:
            raise RuntimeIntentError("source_conflict", "source-only residue differs")
        try:
            binding = validate_source_binding(binding_json)
            snapshot = parse_source_bytes(
                source_bytes,
                source_path=Path(cast("str", binding["source_path"])),
                max_bytes=self._max_source_bytes,
            )
        except PostingJournalError as exc:
            raise _translate(exc) from exc
        if not source_matches_binding(snapshot, binding, allow_relocation=False):
            raise RuntimeIntentError("invalid_input", "source bytes contradict source binding")
        if current_head is not None:
            stored = cast("dict[str, object]", current_head["source_binding"])
            current_without_path = {
                key: value for key, value in binding.items() if key != "source_path"
            }
            stored_without_path = {
                key: value for key, value in stored.items() if key != "source_path"
            }
            if current_without_path != stored_without_path or self._read_source() != source_bytes:
                raise RuntimeIntentError("source_conflict", "source or posting terms changed")
            return copy.deepcopy(current_head)
        if state not in {"empty", "unbound_source"}:
            raise RuntimeIntentError("corrupt_record", "posting ledger cannot be bound")
        head = new_head(canonical_sha256(self._parent.owner.as_json()), binding)
        content = canonical_json_bytes(head)
        if len(content) > self._parent.max_record_bytes:
            raise RuntimeIntentError("record_too_large", "posting head exceeds configured limit")
        namespace = self._require_open()
        existing = existing_source
        if existing is None:
            publish_record_exclusive(namespace.postings, "source.jsonl", source_bytes)
            existing = self._read_source()
        if existing != source_bytes:
            raise RuntimeIntentError("source_conflict", "source-only residue differs")
        published = publish_record_exclusive(namespace.postings, "head.json", content)
        if not published:
            winner = self._head_value()
            if winner != head:
                raise RuntimeIntentError("source_conflict", "another source binding won")
        if self._audit() != "ready":
            raise RuntimeIntentError("storage_failure", "bound posting ledger did not validate")
        result = self._head_value()
        if result is None:
            raise RuntimeIntentError("storage_failure", "posting head was not persisted")
        return result

    def records(self) -> tuple[FrozenPostingRecord, ...]:
        state = self._audit()
        if state in {"empty", "unbound_source"}:
            return ()
        head = self._head_value()
        assert head is not None
        return self._validated_records(head, self._snapshot(head))

    def reserve(  # noqa: PLR0912, PLR0915
        self, selection_json: object, intent: object
    ) -> FrozenPostingRecord:
        head = self._head_value()
        if head is None:
            raise RuntimeIntentError("invalid_input", "posting source is not bound")
        existing = self.records()
        if any(cast("str", record.state) in _UNRESOLVED for record in existing):
            raise RuntimeIntentError("unresolved_posting", "an unresolved posting already exists")
        if not isinstance(intent, TaskPostingIntent):
            raise RuntimeIntentError("invalid_input", "intent must be a TaskPostingIntent")
        if intent.actor_id != self._parent.owner.registered_agent_id:
            raise RuntimeIntentError("invalid_input", "intent actor differs from runtime owner")
        if not isinstance(selection_json, dict) or set(selection_json) != {
            "source_entry_index",
            "source_line_number",
            "source_line_sha256",
            "raw_task",
        }:
            raise RuntimeIntentError("invalid_input", "selection has invalid fields")
        prospective_size = len(
            canonical_json_bytes(
                {
                    "raw_task": selection_json["raw_task"],
                    "intent": intent.as_json(),
                    "diagnostics": [],
                }
            )
        )
        if prospective_size > self._parent.max_record_bytes:
            raise RuntimeIntentError("record_too_large", "posting record exceeds configured limit")
        sequence = cast("int", head["committed_selection_count"])
        binding = cast("Mapping[str, object]", head["source_binding"])
        snapshot = self._snapshot(head)
        count = cast("int", binding["source_entry_count"])
        cycle_index, cycle_position = divmod(sequence, count)
        entry = ordered_entries(snapshot, binding, cycle_index=cycle_index)[cycle_position]
        try:
            supplied = RawTask.from_json(selection_json["raw_task"])
        except PostingJournalError as exc:
            raise _translate(exc) from exc
        if (
            type(selection_json["source_entry_index"]) is not int
            or type(selection_json["source_line_number"]) is not int
            or not isinstance(selection_json["source_line_sha256"], str)
            or selection_json["source_entry_index"] != entry.index
            or selection_json["source_line_number"] != entry.line_number
            or selection_json["source_line_sha256"] != entry.line_sha256
            or supplied != entry.raw_task
        ):
            raise RuntimeIntentError(
                "invalid_input", "selection is not the deterministic source entry"
            )
        terms = cast("Mapping[str, object]", binding["posting_terms"])
        reward = cast("int", terms["base_reward"]) + supplied.level * cast(
            "int", terms["reward_per_level"]
        )
        if (
            intent.title != supplied.title
            or intent.spec != supplied.spec
            or intent.reward != reward
            or any(
                getattr(intent, field) != terms[field]
                for field in (
                    "bidding_deadline_seconds",
                    "acceptance_window_seconds",
                    "execution_deadline_seconds",
                    "review_deadline_seconds",
                )
            )
        ):
            raise RuntimeIntentError("invalid_input", "intent contradicts source posting terms")
        if any(
            record.intent.operation_id == intent.operation_id
            or record.intent.task_id == intent.task_id
            for record in existing
        ):
            raise RuntimeIntentError(
                "operation_conflict", "posting native identity is already used"
            )
        binding_sha256 = cast("str", head["source_binding_sha256"])
        cycle_id = canonical_sha256(
            {"source_binding_sha256": binding_sha256, "cycle_index": cycle_index}
        )
        selection_id = canonical_sha256(
            {
                "cycle_id": cycle_id,
                "cycle_position": cycle_position,
                "source_entry_id": entry.entry_id,
            }
        )
        semantic: dict[str, object] = {
            "schema_version": 1,
            "owner_sha256": head["owner_sha256"],
            "source_binding_sha256": binding_sha256,
            "selection_sequence": sequence,
            "selection_id": selection_id,
            "cycle_index": cycle_index,
            "cycle_id": cycle_id,
            "cycle_position": cycle_position,
            "source_entry_index": entry.index,
            "source_line_number": entry.line_number,
            "source_line_sha256": entry.line_sha256,
            "source_entry_id": entry.entry_id,
            "raw_task_sha256": entry.raw_task_sha256,
            "raw_task": supplied.as_json(),
            "intent": intent.as_json(),
            "request_sha256": intent.request_sha256,
        }
        semantic_sha256 = canonical_sha256(semantic)
        previous_chain = cast("str", head["chain_sha256"])
        selection_chain = canonical_sha256(
            {"previous_chain_sha256": previous_chain, "semantic_sha256": semantic_sha256}
        )
        value = {
            **semantic,
            "semantic_sha256": semantic_sha256,
            "previous_chain_sha256": previous_chain,
            "selection_chain_sha256": selection_chain,
            "revision": 0,
            "state": "prepared",
            "observed_status_code": None,
            "publication": None,
            "diagnostics": [],
        }
        content = canonical_json_bytes(value)
        if len(content) > self._parent.max_record_bytes:
            raise RuntimeIntentError("record_too_large", "posting record exceeds configured limit")
        namespace = self._require_open()
        name = f"{sequence:020d}.json"
        if not publish_record_exclusive(namespace.records, name, content):
            raise RuntimeIntentError("corrupt_record", "posting sequence already exists")
        advanced = copy.deepcopy(head)
        advanced.update(
            {
                "revision": sequence + 1,
                "committed_selection_count": sequence + 1,
                "last_selection_id": selection_id,
                "last_record_semantic_sha256": semantic_sha256,
                "chain_sha256": selection_chain,
            }
        )
        self._replace_head(advanced)
        records = self.records()
        if len(records) != sequence + 1:
            raise RuntimeIntentError("storage_failure", "posting reservation did not validate")
        return records[-1]

    def _current(self, selection_sequence: object) -> FrozenPostingRecord:
        sequence = require_integer(selection_sequence, "selection_sequence")
        records = self.records()
        if sequence >= len(records):
            raise RuntimeIntentError("invalid_input", "posting record does not exist")
        return records[sequence]

    def _replace_record(self, value: Mapping[str, object]) -> FrozenPostingRecord:
        namespace = self._require_open()
        sequence = cast("int", value["selection_sequence"])
        name = f"{sequence:020d}.json"
        content = canonical_json_bytes(value)
        if len(content) > self._parent.max_record_bytes:
            raise RuntimeIntentError("record_too_large", "posting record exceeds configured limit")
        replace_record(namespace.records, name, content)
        return self._current(sequence)

    def record_observation(
        self,
        selection_sequence: int,
        expected_revision: int,
        response: object,
        *,
        observation: str,
    ) -> FrozenPostingRecord:
        current = self._current(selection_sequence)
        expected = require_integer(expected_revision, "expected_revision")
        if current.revision != expected:
            raise RuntimeIntentError("stale_revision", "posting revision is no longer current")
        try:
            status, publication, state = normalize_observation(
                response,
                intent=current.intent,
                observation=cast("Any", observation),
            )
        except PostingJournalError as exc:
            raise _translate(exc) from exc
        current_state = cast("str", current.state)
        if current_state in {"published", "blocked_integrity"}:
            if publication == current.publication and state == current_state:
                return current
            raise RuntimeIntentError("invalid_acknowledgment", "terminal posting cannot regress")
        if current_state == "attention_required" and observation != "status":
            raise RuntimeIntentError(
                "invalid_acknowledgment", "attention posting requires status reconciliation"
            )
        allowed = state in {
            "awaiting_lock",
            "published",
            "blocked_integrity",
        } and current_state in {"prepared", "awaiting_lock", "attention_required"}
        if not allowed:
            raise RuntimeIntentError("invalid_acknowledgment", "posting transition is invalid")
        value = current.as_json()
        value.update(
            {
                "revision": cast("int", current.revision) + 1,
                "state": state,
                "observed_status_code": status,
                "publication": publication,
            }
        )
        return self._replace_record(value)

    def record_diagnostic(
        self,
        selection_sequence: int,
        expected_revision: int,
        *,
        code: str,
        status_code: int | None,
        attention: bool,
    ) -> FrozenPostingRecord:
        current = self._current(selection_sequence)
        expected = require_integer(expected_revision, "expected_revision")
        if current.revision != expected:
            raise RuntimeIntentError("stale_revision", "posting revision is no longer current")
        valid = (
            (code == "transport_error" and status_code is None and attention is False)
            or (
                code == "invalid_acknowledgment"
                and (
                    status_code is None or (type(status_code) is int and 100 <= status_code <= 599)
                )
                and attention is True
            )
            or (
                code == "http_rejection"
                and type(status_code) is int
                and 400 <= status_code <= 599
                and attention is (status_code < 500)
            )
            or (
                code == "integrity_blocked"
                and current.state == "blocked_integrity"
                and type(status_code) is int
                and status_code in {200, 409}
                and attention is True
            )
        )
        if not valid or type(attention) is not bool:
            raise RuntimeIntentError("invalid_input", "diagnostic arguments contradict mapping")
        state = cast("str", current.state)
        if state in {"prepared", "awaiting_lock"} and (
            code == "invalid_acknowledgment"
            or (code == "http_rejection" and cast("int", status_code) < 500)
        ):
            state = "attention_required"
        diagnostics = current.as_json()["diagnostics"]
        assert isinstance(diagnostics, list)
        diagnostics.append({"code": code, "status_code": status_code, "attention": attention})
        diagnostics = diagnostics[-self._parent.diagnostic_limit :]
        value = current.as_json()
        value.update(
            {
                "revision": cast("int", current.revision) + 1,
                "state": state,
                "diagnostics": diagnostics,
            }
        )
        return self._replace_record(value)


__all__ = ["FileTaskPostingStore", "FrozenPostingRecord"]
