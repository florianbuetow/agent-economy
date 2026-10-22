"""Pure formats and validation for the durable feeder posting ledger."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

import httpx

from service_auth.financial_responses import SQLITE_MAX_INTEGER
from service_auth.task_postings import (
    TaskPostingIntent,
    TaskPostingResponse,
    parse_task_posting_status_response,
    parse_task_posting_submit_response,
)

SCHEMA_VERSION = 1
SELECTION_ALGORITHM = "feeder-cycle-sha256-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECORD_NAME = re.compile(r"^[0-9]{20}\.json$")
_MAX_DURATION_SECONDS = SQLITE_MAX_INTEGER // 1_000_000
_RAW_KEYS = frozenset({"title", "spec", "solutions", "level", "problem_type", "solution_note"})
_BINDING_KEYS = frozenset(
    {
        "source_path",
        "source_snapshot_sha256",
        "source_size_bytes",
        "source_entry_count",
        "posting_terms",
    }
)
_TERM_KEYS = frozenset(
    {
        "base_reward",
        "reward_per_level",
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
        "shuffle",
    }
)
_HEAD_KEYS = frozenset(
    {
        "schema_version",
        "revision",
        "owner_sha256",
        "source_binding",
        "source_binding_sha256",
        "selection_algorithm",
        "committed_selection_count",
        "last_selection_id",
        "last_record_semantic_sha256",
        "chain_sha256",
    }
)
_SEMANTIC_KEYS = frozenset(
    {
        "schema_version",
        "owner_sha256",
        "source_binding_sha256",
        "selection_sequence",
        "selection_id",
        "cycle_index",
        "cycle_id",
        "cycle_position",
        "source_entry_index",
        "source_line_number",
        "source_line_sha256",
        "source_entry_id",
        "raw_task_sha256",
        "raw_task",
        "intent",
        "request_sha256",
    }
)
_RECORD_KEYS = _SEMANTIC_KEYS | frozenset(
    {
        "semantic_sha256",
        "previous_chain_sha256",
        "selection_chain_sha256",
        "revision",
        "state",
        "observed_status_code",
        "publication",
        "diagnostics",
    }
)
_STATES = frozenset(
    {"prepared", "awaiting_lock", "published", "blocked_integrity", "attention_required"}
)


class PostingJournalError(ValueError):
    """A typed pure-format validation failure."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


def canonical_json_bytes(value: object) -> bytes:
    """Return the one canonical JSON encoding used by posting artifacts."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PostingJournalError("invalid_input", "value is not finite canonical JSON") from exc


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= SQLITE_MAX_INTEGER:
        raise PostingJournalError("invalid_input", f"{field} is not a supported integer")
    return value


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PostingJournalError("invalid_input", f"{field} is not a SHA-256 digest")
    return value


def _exact_object(value: object, keys: frozenset[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PostingJournalError("invalid_input", f"{field} has an invalid shape")
    return cast("dict[str, object]", value)


@dataclass(frozen=True, slots=True)
class RawTask:
    """One normalized immutable source task."""

    title: str
    spec: str
    solutions: tuple[str, ...]
    level: int
    problem_type: str
    solution_note: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not isinstance(self.spec, str):
            raise PostingJournalError("invalid_input", "raw task title and spec must be strings")
        if not isinstance(self.solutions, (list, tuple)) or any(
            not isinstance(item, str) for item in self.solutions
        ):
            raise PostingJournalError("invalid_input", "raw task solutions must be strings")
        if type(self.level) is not int:
            raise PostingJournalError("invalid_input", "raw task level must be an integer")
        if not isinstance(self.problem_type, str):
            raise PostingJournalError("invalid_input", "raw task problem_type must be a string")
        if self.solution_note is not None and not isinstance(self.solution_note, str):
            raise PostingJournalError(
                "invalid_input", "raw task solution_note must be text or null"
            )
        object.__setattr__(self, "solutions", tuple(self.solutions))

    @classmethod
    def from_json(cls, value: object) -> RawTask:
        if not isinstance(value, dict):
            raise PostingJournalError("invalid_input", "raw_task must be an object")
        if set(value) != _RAW_KEYS:
            raise PostingJournalError("invalid_input", "raw_task has invalid fields")
        title = value["title"]
        spec = value["spec"]
        solutions = value["solutions"]
        level = value["level"]
        problem_type = value["problem_type"]
        solution_note = value["solution_note"]
        if not isinstance(solutions, list) or any(not isinstance(item, str) for item in solutions):
            raise PostingJournalError("invalid_input", "raw task solutions must be an array")
        return cls(
            cast("str", title),
            cast("str", spec),
            tuple(solutions),
            cast("int", level),
            cast("str", problem_type),
            cast("str | None", solution_note),
        )

    def as_json(self) -> dict[str, object]:
        return {
            "title": self.title,
            "spec": self.spec,
            "solutions": list(self.solutions),
            "level": self.level,
            "problem_type": self.problem_type,
            "solution_note": self.solution_note,
        }


@dataclass(frozen=True, slots=True)
class TaskSourceEntry:
    index: int
    line_number: int
    line_sha256: str
    raw_task_sha256: str
    entry_id: str
    raw_task: RawTask

    def selection_json(self) -> dict[str, object]:
        return {
            "source_entry_index": self.index,
            "source_line_number": self.line_number,
            "source_line_sha256": self.line_sha256,
            "raw_task": self.raw_task.as_json(),
        }


@dataclass(frozen=True, slots=True)
class TaskSourceSnapshot:
    source_path: Path
    source_snapshot_sha256: str
    source_size_bytes: int
    source_bytes: bytes
    entries: tuple[TaskSourceEntry, ...]

    @property
    def source_entry_count(self) -> int:
        return len(self.entries)


def parse_source_bytes(
    source_bytes: bytes, *, source_path: Path, max_bytes: int
) -> TaskSourceSnapshot:
    """Parse one exact bounded UTF-8 JSONL snapshot without filesystem access."""

    if not isinstance(source_bytes, bytes) or not isinstance(source_path, Path):
        raise PostingJournalError("invalid_input", "source bytes and path have invalid types")
    _integer(max_bytes, "max_bytes", minimum=1)
    if not source_path.is_absolute():
        raise PostingJournalError("invalid_input", "source_path must be absolute")
    if len(source_bytes) > max_bytes:
        raise PostingJournalError("record_too_large", "source snapshot exceeds configured limit")
    if not source_bytes:
        raise PostingJournalError("invalid_input", "source snapshot is empty")
    try:
        source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PostingJournalError("invalid_input", "source snapshot is not UTF-8") from exc
    snapshot_sha256 = hashlib.sha256(source_bytes).hexdigest()
    entries: list[TaskSourceEntry] = []
    for line_number, line_bytes in enumerate(source_bytes.splitlines(keepends=True), start=1):
        try:
            line_text = line_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:  # guarded above; retains a local typed boundary
            raise PostingJournalError("invalid_input", "source line is not UTF-8") from exc
        if not line_text.strip():
            continue
        try:
            raw_value = json.loads(line_text)
        except json.JSONDecodeError as exc:
            raise PostingJournalError(
                "invalid_input", "source snapshot contains malformed JSON"
            ) from exc
        if isinstance(raw_value, dict) and "solution_note" not in raw_value:
            raw_value = {**raw_value, "solution_note": None}
        raw_task = RawTask.from_json(raw_value)
        raw_json = raw_task.as_json()
        raw_sha256 = canonical_sha256(raw_json)
        line_sha256 = hashlib.sha256(line_bytes).hexdigest()
        index = len(entries)
        entry_id = canonical_sha256(
            {
                "snapshot_sha256": snapshot_sha256,
                "nonblank_entry_index": index,
                "physical_line_number": line_number,
                "exact_line_sha256": line_sha256,
                "raw_task_sha256": raw_sha256,
            }
        )
        entries.append(
            TaskSourceEntry(
                index=index,
                line_number=line_number,
                line_sha256=line_sha256,
                raw_task_sha256=raw_sha256,
                entry_id=entry_id,
                raw_task=raw_task,
            )
        )
    if not entries:
        raise PostingJournalError("invalid_input", "source snapshot has no task entries")
    return TaskSourceSnapshot(
        source_path=source_path,
        source_snapshot_sha256=snapshot_sha256,
        source_size_bytes=len(source_bytes),
        source_bytes=source_bytes,
        entries=tuple(entries),
    )


def validate_source_binding(value: object) -> dict[str, object]:
    binding = _exact_object(value, _BINDING_KEYS, "source_binding")
    source_path = binding["source_path"]
    if (
        not isinstance(source_path, str)
        or not Path(source_path).is_absolute()
        or Path(source_path).anchor != os.sep
        or ".." in Path(source_path).parts
    ):
        raise PostingJournalError("invalid_input", "source_path must be an absolute path")
    snapshot_sha256 = _sha256(binding["source_snapshot_sha256"], "source_snapshot_sha256")
    size = _integer(binding["source_size_bytes"], "source_size_bytes")
    count = _integer(binding["source_entry_count"], "source_entry_count", minimum=1)
    terms = _exact_object(binding["posting_terms"], _TERM_KEYS, "posting_terms")
    base_reward = terms["base_reward"]
    reward_per_level = terms["reward_per_level"]
    if type(base_reward) is not int or type(reward_per_level) is not int:
        raise PostingJournalError("invalid_input", "reward terms must be strict integers")
    durations: dict[str, int] = {}
    for field in (
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
    ):
        value_int = terms[field]
        if type(value_int) is not int or not 1 <= value_int <= _MAX_DURATION_SECONDS:
            raise PostingJournalError("invalid_input", f"{field} is outside SDK bounds")
        durations[field] = value_int
    shuffle = terms["shuffle"]
    if type(shuffle) is not bool:
        raise PostingJournalError("invalid_input", "shuffle must be a boolean")
    return {
        "source_path": source_path,
        "source_snapshot_sha256": snapshot_sha256,
        "source_size_bytes": size,
        "source_entry_count": count,
        "posting_terms": {
            "base_reward": base_reward,
            "reward_per_level": reward_per_level,
            **durations,
            "shuffle": shuffle,
        },
    }


def source_matches_binding(
    snapshot: TaskSourceSnapshot, binding: Mapping[str, object], *, allow_relocation: bool
) -> bool:
    if not allow_relocation and str(snapshot.source_path) != binding["source_path"]:
        return False
    return (
        snapshot.source_snapshot_sha256 == binding["source_snapshot_sha256"]
        and snapshot.source_size_bytes == binding["source_size_bytes"]
        and snapshot.source_entry_count == binding["source_entry_count"]
    )


def ordered_entries(
    snapshot: TaskSourceSnapshot,
    binding: Mapping[str, object],
    *,
    cycle_index: int,
) -> tuple[TaskSourceEntry, ...]:
    binding_sha256 = canonical_sha256(binding)
    cycle_id = canonical_sha256(
        {"source_binding_sha256": binding_sha256, "cycle_index": cycle_index}
    )
    terms = cast("Mapping[str, object]", binding["posting_terms"])
    if terms["shuffle"] is False:
        return snapshot.entries
    return tuple(
        sorted(
            snapshot.entries,
            key=lambda entry: (
                canonical_sha256({"cycle_id": cycle_id, "source_entry_id": entry.entry_id}),
                entry.index,
            ),
        )
    )


def genesis_chain(owner_sha256: str, source_binding_sha256: str) -> str:
    return canonical_sha256(
        {
            "schema_version": SCHEMA_VERSION,
            "owner_sha256": owner_sha256,
            "source_binding_sha256": source_binding_sha256,
        }
    )


def new_head(owner_sha256: str, binding: Mapping[str, object]) -> dict[str, object]:
    binding_json = copy.deepcopy(dict(binding))
    binding_sha256 = canonical_sha256(binding_json)
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "owner_sha256": owner_sha256,
        "source_binding": binding_json,
        "source_binding_sha256": binding_sha256,
        "selection_algorithm": SELECTION_ALGORITHM,
        "committed_selection_count": 0,
        "last_selection_id": None,
        "last_record_semantic_sha256": None,
        "chain_sha256": genesis_chain(owner_sha256, binding_sha256),
    }


def parse_head(value: object) -> dict[str, object]:
    head = _exact_object(value, _HEAD_KEYS, "head")
    if type(head["schema_version"]) is not int or head["schema_version"] != SCHEMA_VERSION:
        raise PostingJournalError("corrupt_record", "head schema version is invalid")
    revision = _integer(head["revision"], "revision")
    count = _integer(head["committed_selection_count"], "committed_selection_count")
    if revision != count:
        raise PostingJournalError("corrupt_record", "head revision and count differ")
    owner_sha256 = _sha256(head["owner_sha256"], "owner_sha256")
    binding = validate_source_binding(head["source_binding"])
    binding_sha256 = _sha256(head["source_binding_sha256"], "source_binding_sha256")
    chain_sha256 = _sha256(head["chain_sha256"], "chain_sha256")
    if (
        canonical_sha256(binding) != binding_sha256
        or head["selection_algorithm"] != SELECTION_ALGORITHM
    ):
        raise PostingJournalError("corrupt_record", "head source binding is invalid")
    last_selection = head["last_selection_id"]
    last_semantic = head["last_record_semantic_sha256"]
    if count == 0:
        if last_selection is not None or last_semantic is not None:
            raise PostingJournalError("corrupt_record", "empty head has a tail identity")
        if chain_sha256 != genesis_chain(owner_sha256, binding_sha256):
            raise PostingJournalError("corrupt_record", "genesis chain is invalid")
    else:
        _sha256(last_selection, "last_selection_id")
        _sha256(last_semantic, "last_record_semantic_sha256")
    return copy.deepcopy(head)


def decode_canonical(content: bytes, *, identity: str) -> object:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PostingJournalError("corrupt_record", f"{identity} is not valid JSON") from exc
    if canonical_json_bytes(value) != content:
        raise PostingJournalError("corrupt_record", f"{identity} is not canonical JSON")
    return value


def _diagnostics(value: object, *, limit: int) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise PostingJournalError("corrupt_record", "diagnostics are invalid or unbounded")
    result: list[Mapping[str, object]] = []
    for item in value:
        diagnostic = _exact_object(
            item, frozenset({"code", "status_code", "attention"}), "diagnostic"
        )
        code = diagnostic["code"]
        status = diagnostic["status_code"]
        attention = diagnostic["attention"]
        valid = (
            (code == "transport_error" and status is None and attention is False)
            or (
                code == "invalid_acknowledgment"
                and (status is None or (type(status) is int and 100 <= status <= 599))
                and attention is True
            )
            or (
                code == "http_rejection"
                and type(status) is int
                and 400 <= status <= 599
                and attention is (status < 500)
            )
            or (
                code == "integrity_blocked"
                and type(status) is int
                and status in {200, 409}
                and attention is True
            )
        )
        if not valid:
            raise PostingJournalError("corrupt_record", "diagnostic mapping is invalid")
        result.append(MappingProxyType(copy.deepcopy(diagnostic)))
    return tuple(result)


def _validate_publication(
    publication: object, *, status_code: object, intent: TaskPostingIntent
) -> tuple[dict[str, object], str]:
    if type(status_code) is not int:
        raise PostingJournalError("corrupt_record", "observation status is invalid")
    request = httpx.Request("GET" if status_code == 200 else "POST", "https://journal.invalid")
    try:
        if status_code == 200:
            response = httpx.Response(200, request=request, json=publication)
            parsed = parse_task_posting_status_response(response, intent.operation_id)
        elif status_code == 409:
            wrapper = {
                "error": "publication_integrity_blocked",
                "message": "Publication evidence requires reconciliation",
                "details": {"publication": publication},
            }
            response = httpx.Response(409, request=request, json=wrapper)
            parsed = parse_task_posting_submit_response(response, intent)
        elif status_code in {201, 202}:
            response = httpx.Response(status_code, request=request, json=publication)
            parsed = parse_task_posting_submit_response(response, intent)
        else:
            raise PostingJournalError("corrupt_record", "observation status is unsupported")
        envelope_value = (
            cast("dict[str, object]", parsed.body["details"])["publication"]
            if status_code == 409
            else parsed.body
        )
        if not isinstance(envelope_value, dict):
            raise RuntimeError("publication envelope is invalid")
        state = envelope_value.get("publication_state")
        if not isinstance(state, str):
            raise RuntimeError("publication state is invalid")
        binding_status = {"awaiting_lock": 202, "published": 201, "blocked_integrity": 409}.get(
            state
        )
        if binding_status is None:
            raise RuntimeError("publication state is invalid")
        if binding_status == 409:
            binding_body: object = {
                "error": "publication_integrity_blocked",
                "message": "Publication evidence requires reconciliation",
                "details": {"publication": envelope_value},
            }
        else:
            binding_body = envelope_value
        parse_task_posting_submit_response(
            httpx.Response(binding_status, request=request, json=binding_body), intent
        )
    except (RuntimeError, ValueError, TypeError, httpx.HTTPError) as exc:
        raise PostingJournalError("corrupt_record", "publication observation is invalid") from exc
    envelope = (
        cast("dict[str, object]", parsed.body["details"])["publication"]
        if status_code == 409
        else parsed.body
    )
    assert isinstance(envelope, dict)
    return copy.deepcopy(cast("dict[str, object]", envelope)), cast(
        "str", envelope["publication_state"]
    )


def normalize_observation(
    response: object,
    *,
    intent: TaskPostingIntent,
    observation: Literal["submit", "status"],
) -> tuple[int, dict[str, object], str]:
    """Strictly reparse one typed SDK observation and return its envelope."""

    if not isinstance(response, TaskPostingResponse):
        raise PostingJournalError("invalid_acknowledgment", "response has the wrong type")
    status_code = response.status_code
    request = httpx.Request("POST" if observation == "submit" else "GET", "https://journal.invalid")
    try:
        if observation == "submit":
            wire_body: object = response.body
            parsed = parse_task_posting_submit_response(
                httpx.Response(status_code, request=request, json=wire_body), intent
            )
            if status_code == 409:
                details = parsed.body.get("details")
                if not isinstance(details, dict):
                    raise RuntimeError("blocked wrapper details are invalid")
                envelope = details.get("publication")
            else:
                envelope = parsed.body
        elif observation == "status":
            parsed = parse_task_posting_status_response(
                httpx.Response(status_code, request=request, json=response.body),
                intent.operation_id,
            )
            envelope = parsed.body
        else:
            raise PostingJournalError("invalid_acknowledgment", "observation is unsupported")
        if not isinstance(envelope, dict):
            raise RuntimeError("publication envelope is invalid")
        validated, state = _validate_publication(envelope, status_code=status_code, intent=intent)
        return status_code, validated, state
    except PostingJournalError:
        raise
    except (RuntimeError, ValueError, TypeError, httpx.HTTPError) as exc:
        raise PostingJournalError(
            "invalid_acknowledgment", "task posting acknowledgment is invalid"
        ) from exc


@dataclass(frozen=True, slots=True)
class ValidatedPostingRecord:
    value: Mapping[str, object]
    intent: TaskPostingIntent
    raw_task: RawTask

    def as_json(self) -> dict[str, object]:
        return copy.deepcopy(dict(self.value))


def validate_record(  # noqa: PLR0912, PLR0915
    value: object,
    *,
    snapshot: TaskSourceSnapshot,
    head: Mapping[str, object],
    expected_sequence: int,
    previous_chain_sha256: str,
    diagnostic_limit: int,
) -> ValidatedPostingRecord:
    record = _exact_object(value, _RECORD_KEYS, "posting record")
    try:
        if type(record["schema_version"]) is not int or record["schema_version"] != 1:
            raise PostingJournalError("corrupt_record", "record schema version is invalid")
        sequence = _integer(record["selection_sequence"], "selection_sequence")
        revision = _integer(record["revision"], "revision")
        if sequence != expected_sequence:
            raise PostingJournalError("corrupt_record", "record sequence is not contiguous")
        owner_sha256 = _sha256(record["owner_sha256"], "owner_sha256")
        binding_sha256 = _sha256(record["source_binding_sha256"], "source_binding_sha256")
        if owner_sha256 != head["owner_sha256"] or binding_sha256 != head["source_binding_sha256"]:
            raise PostingJournalError("corrupt_record", "record owner or source differs")
        binding = cast("Mapping[str, object]", head["source_binding"])
        count = cast("int", binding["source_entry_count"])
        cycle_index, cycle_position = divmod(sequence, count)
        stored_cycle_index = _integer(record["cycle_index"], "cycle_index")
        stored_cycle_position = _integer(record["cycle_position"], "cycle_position")
        stored_entry_index = _integer(record["source_entry_index"], "source_entry_index")
        stored_line_number = _integer(record["source_line_number"], "source_line_number", minimum=1)
        cycle_id = canonical_sha256(
            {"source_binding_sha256": binding_sha256, "cycle_index": cycle_index}
        )
        if (
            stored_cycle_index != cycle_index
            or stored_cycle_position != cycle_position
            or record["cycle_id"] != cycle_id
        ):
            raise PostingJournalError("corrupt_record", "record cycle identity is invalid")
        ordered = ordered_entries(snapshot, binding, cycle_index=cycle_index)
        entry = ordered[cycle_position]
        raw_task = RawTask.from_json(record["raw_task"])
        selection_id = canonical_sha256(
            {
                "cycle_id": cycle_id,
                "cycle_position": cycle_position,
                "source_entry_id": entry.entry_id,
            }
        )
        if (
            stored_entry_index != entry.index
            or stored_line_number != entry.line_number
            or record["source_line_sha256"] != entry.line_sha256
            or record["source_entry_id"] != entry.entry_id
            or record["raw_task_sha256"] != entry.raw_task_sha256
            or raw_task != entry.raw_task
            or record["selection_id"] != selection_id
        ):
            raise PostingJournalError("corrupt_record", "record is absent from source catalog")
        intent = TaskPostingIntent.from_json(record["intent"])
        terms = cast("Mapping[str, object]", binding["posting_terms"])
        reward = cast("int", terms["base_reward"]) + raw_task.level * cast(
            "int", terms["reward_per_level"]
        )
        if (
            intent.title != raw_task.title
            or intent.spec != raw_task.spec
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
            raise PostingJournalError("corrupt_record", "intent contradicts frozen source terms")
        if record["request_sha256"] != intent.request_sha256:
            raise PostingJournalError("corrupt_record", "request hash is invalid")
        semantic = {key: record[key] for key in _SEMANTIC_KEYS}
        semantic_sha256 = canonical_sha256(semantic)
        chain_sha256 = canonical_sha256(
            {"previous_chain_sha256": previous_chain_sha256, "semantic_sha256": semantic_sha256}
        )
        if (
            record["semantic_sha256"] != semantic_sha256
            or record["previous_chain_sha256"] != previous_chain_sha256
            or record["selection_chain_sha256"] != chain_sha256
        ):
            raise PostingJournalError("corrupt_record", "record hash chain is invalid")
        state = record["state"]
        if type(state) is not str or state not in _STATES:
            raise PostingJournalError("corrupt_record", "record state is invalid")
        diagnostics = _diagnostics(record["diagnostics"], limit=diagnostic_limit)
        publication = record["publication"]
        status = record["observed_status_code"]
        if publication is None:
            if status is not None or state in {"awaiting_lock", "published", "blocked_integrity"}:
                raise PostingJournalError("corrupt_record", "record observation is missing")
            observed_state: str | None = None
        else:
            envelope, observed_state = _validate_publication(
                publication, status_code=status, intent=intent
            )
            if envelope != publication or not (
                observed_state == state
                or (state == "attention_required" and observed_state == "awaiting_lock")
            ):
                raise PostingJournalError("corrupt_record", "record state contradicts publication")
        if revision == 0 and not (
            state == "prepared" and publication is None and status is None and not diagnostics
        ):
            raise PostingJournalError("corrupt_record", "initial record state is invalid")
        if revision < len(diagnostics) + (publication is not None):
            raise PostingJournalError("corrupt_record", "record revision is incoherent")
        if state == "prepared" and (publication is not None or (revision > 0 and not diagnostics)):
            raise PostingJournalError("corrupt_record", "prepared record evidence is invalid")
        if any(item["code"] == "integrity_blocked" for item in diagnostics) and state != (
            "blocked_integrity"
        ):
            raise PostingJournalError("corrupt_record", "integrity diagnostic state is invalid")
        return ValidatedPostingRecord(
            value=MappingProxyType(copy.deepcopy(record)), intent=intent, raw_task=raw_task
        )
    except PostingJournalError:
        raise
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise PostingJournalError("corrupt_record", "posting record is invalid") from exc


@dataclass(frozen=True, slots=True)
class PostingLedgerFinding:
    reason: str
    identity: str


@dataclass(frozen=True, slots=True)
class PostingLedgerAudit:
    state: Literal["empty", "unbound_source", "ready", "adoption_required", "invalid"]
    findings: tuple[PostingLedgerFinding, ...]


def _owner_identity(owner_json: object) -> tuple[str, str]:
    keys = frozenset(
        {
            "schema_version",
            "environment_id",
            "role",
            "profile",
            "handle",
            "registered_agent_id",
            "key_fingerprint",
            "service_urls",
        }
    )
    owner = _exact_object(owner_json, keys, "owner")
    if type(owner["schema_version"]) is not int or owner["schema_version"] != 1:
        raise PostingJournalError("invalid_input", "owner schema is invalid")
    actor_id = owner["registered_agent_id"]
    if not isinstance(actor_id, str) or actor_id == "":
        raise PostingJournalError("invalid_input", "owner actor is invalid")
    return canonical_sha256(owner), actor_id


def audit_posting_ledger(  # noqa: PLR0911, PLR0912, PLR0915
    *,
    owner_json: object,
    source_bytes: bytes | None,
    head_bytes: bytes | None,
    record_bytes: Mapping[str, bytes],
    max_source_bytes: int,
    max_record_bytes: int,
    diagnostic_limit: int,
) -> PostingLedgerAudit:
    """Audit exact artifact bytes without I/O, repair, or runtime imports."""

    _integer(max_source_bytes, "max_source_bytes", minimum=1)
    _integer(max_record_bytes, "max_record_bytes", minimum=1)
    _integer(diagnostic_limit, "diagnostic_limit", minimum=1)
    if source_bytes is not None and not isinstance(source_bytes, bytes):
        raise PostingJournalError("invalid_input", "source_bytes must be bytes or null")
    if head_bytes is not None and not isinstance(head_bytes, bytes):
        raise PostingJournalError("invalid_input", "head_bytes must be bytes or null")
    if not isinstance(record_bytes, Mapping) or any(
        not isinstance(name, str) or not isinstance(content, bytes)
        for name, content in record_bytes.items()
    ):
        raise PostingJournalError("invalid_input", "record_bytes must map names to bytes")
    owner_sha256, actor_id = _owner_identity(owner_json)
    if source_bytes is None and head_bytes is None and not record_bytes:
        return PostingLedgerAudit("empty", ())
    findings: list[PostingLedgerFinding] = []

    def fail(reason: str, identity: str) -> None:
        findings.append(PostingLedgerFinding(reason, identity))

    if source_bytes is None:
        fail("corrupt_record", "source.jsonl")
        return PostingLedgerAudit("invalid", tuple(findings))
    if len(source_bytes) > max_source_bytes:
        fail("record_too_large", "source.jsonl")
        return PostingLedgerAudit("invalid", tuple(findings))
    try:
        provisional_path = Path("/retained/source.jsonl")
        snapshot = parse_source_bytes(
            source_bytes, source_path=provisional_path, max_bytes=max_source_bytes
        )
    except PostingJournalError as exc:
        fail(exc.reason if exc.reason == "record_too_large" else "corrupt_record", "source.jsonl")
        return PostingLedgerAudit("invalid", tuple(findings))
    if head_bytes is None:
        if record_bytes:
            fail("corrupt_record", sorted(record_bytes)[0])
            return PostingLedgerAudit("invalid", tuple(findings))
        return PostingLedgerAudit("unbound_source", ())
    if len(head_bytes) > max_record_bytes:
        fail("record_too_large", "head.json")
        return PostingLedgerAudit("invalid", tuple(findings))
    try:
        head = parse_head(decode_canonical(head_bytes, identity="head.json"))
        if head["owner_sha256"] != owner_sha256:
            raise PostingJournalError("owner_conflict", "head owner differs")
        binding = cast("Mapping[str, object]", head["source_binding"])
        rebound = TaskSourceSnapshot(
            source_path=Path(cast("str", binding["source_path"])),
            source_snapshot_sha256=snapshot.source_snapshot_sha256,
            source_size_bytes=snapshot.source_size_bytes,
            source_bytes=snapshot.source_bytes,
            entries=snapshot.entries,
        )
        if not source_matches_binding(rebound, binding, allow_relocation=False):
            raise PostingJournalError("corrupt_record", "source snapshot differs from head")
        snapshot = rebound
    except PostingJournalError as exc:
        fail(exc.reason if exc.reason == "owner_conflict" else "corrupt_record", "head.json")
        return PostingLedgerAudit("invalid", tuple(findings))
    names = sorted(record_bytes)
    if any(_RECORD_NAME.fullmatch(name) is None for name in names):
        fail("corrupt_record", next(name for name in names if _RECORD_NAME.fullmatch(name) is None))
        return PostingLedgerAudit("invalid", tuple(findings))
    count = cast("int", head["committed_selection_count"])
    if len(names) not in {count, count + 1}:
        fail("corrupt_record", names[-1] if names else "head.json")
        return PostingLedgerAudit("invalid", tuple(findings))
    previous = genesis_chain(owner_sha256, cast("str", head["source_binding_sha256"]))
    records: list[ValidatedPostingRecord] = []
    for sequence, name in enumerate(names):
        if name != f"{sequence:020d}.json":
            fail("corrupt_record", name)
            return PostingLedgerAudit("invalid", tuple(findings))
        content = record_bytes[name]
        if len(content) > max_record_bytes:
            fail("record_too_large", name)
            return PostingLedgerAudit("invalid", tuple(findings))
        try:
            record = validate_record(
                decode_canonical(content, identity=name),
                snapshot=snapshot,
                head=head,
                expected_sequence=sequence,
                previous_chain_sha256=previous,
                diagnostic_limit=diagnostic_limit,
            )
            if record.intent.actor_id != actor_id:
                raise PostingJournalError("owner_conflict", "record actor differs from owner")
        except PostingJournalError as exc:
            fail(exc.reason if exc.reason == "owner_conflict" else "corrupt_record", name)
            return PostingLedgerAudit("invalid", tuple(findings))
        records.append(record)
        previous = cast("str", record.value["selection_chain_sha256"])
    if count:
        tail = records[count - 1]
        if (
            head["chain_sha256"] != tail.value["selection_chain_sha256"]
            or head["last_selection_id"] != tail.value["selection_id"]
            or head["last_record_semantic_sha256"] != tail.value["semantic_sha256"]
        ):
            fail("corrupt_record", "head.json")
            return PostingLedgerAudit("invalid", tuple(findings))
    operations: set[str] = set()
    tasks: set[str] = set()
    for index, record in enumerate(records):
        if record.intent.operation_id in operations or record.intent.task_id in tasks:
            fail("corrupt_record", f"{index:020d}.json")
            return PostingLedgerAudit("invalid", tuple(findings))
        operations.add(record.intent.operation_id)
        tasks.add(record.intent.task_id)
    if len(records) == count + 1:
        successor = records[-1].value
        if not (
            successor["state"] == "prepared"
            and successor["revision"] == 0
            and successor["observed_status_code"] is None
            and successor["publication"] is None
            and successor["diagnostics"] == []
        ):
            fail("corrupt_record", names[-1])
            return PostingLedgerAudit("invalid", tuple(findings))
        return PostingLedgerAudit("adoption_required", ())
    return PostingLedgerAudit("ready", ())


__all__ = [
    "PostingJournalError",
    "PostingLedgerAudit",
    "PostingLedgerFinding",
    "RawTask",
    "TaskSourceEntry",
    "TaskSourceSnapshot",
    "ValidatedPostingRecord",
    "audit_posting_ledger",
    "canonical_json_bytes",
    "canonical_sha256",
    "decode_canonical",
    "genesis_chain",
    "new_head",
    "normalize_observation",
    "ordered_entries",
    "parse_head",
    "parse_source_bytes",
    "source_matches_binding",
    "validate_record",
    "validate_source_binding",
]
