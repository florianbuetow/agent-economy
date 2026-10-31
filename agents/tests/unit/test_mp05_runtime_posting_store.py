# ruff: noqa: SIM117
"""MP-05 durable feeder posting-ledger contract.

The module is loaded dynamically so this test file still collects while the new
runtime capability is absent.  Those failures are interface capability gaps;
they are not represented as proof of a financial duplication defect.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import os
import stat
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest
from service_auth.task_postings import TaskPostingResponse

from base_agent.runtime_intents import FileTaskIntentStore, RuntimeIntentError
from tests.fakes.mp05_posting_fixtures import (
    SOURCE_BYTES,
    canonical_bytes,
    canonical_sha256,
    posting_intent,
    posting_response,
    raw_task,
    runtime_owner,
    selection,
    source_binding,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_MODULE_NAME = "base_agent.runtime_postings"
_CODEC_MODULE_NAME = "service_auth.posting_journal"
_REQUIRED_EXPORTS = {"FileTaskPostingStore", "FrozenPostingRecord"}
_module_spec = importlib.util.find_spec(_MODULE_NAME)
_postings: Any | None = importlib.import_module(_MODULE_NAME) if _module_spec is not None else None
_codec_spec = importlib.util.find_spec(_CODEC_MODULE_NAME)
_codec: Any | None = (
    importlib.import_module(_CODEC_MODULE_NAME) if _codec_spec is not None else None
)
_missing_exports = (
    sorted(_REQUIRED_EXPORTS.difference(dir(_postings))) if _postings is not None else []
)

_HEAD_KEYS = {
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
_SEMANTIC_KEYS = {
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
_RECORD_KEYS = _SEMANTIC_KEYS | {
    "semantic_sha256",
    "previous_chain_sha256",
    "selection_chain_sha256",
    "revision",
    "state",
    "observed_status_code",
    "publication",
    "diagnostics",
}


@pytest.fixture(autouse=True)
def _required_posting_interface(request: pytest.FixtureRequest) -> None:
    if request.node.name == "test_runtime_posting_store_interface_is_available":
        return
    assert _postings is not None, (
        "MP-05 capability gap: base_agent.runtime_postings has not been implemented"
    )
    assert _codec is not None, (
        "MP-05 capability gap: service_auth.posting_journal has not been implemented"
    )
    assert _missing_exports == [], f"MP-05 posting interface is incomplete: {_missing_exports}"


def _intent_store(
    root: Path,
    *,
    owner: object | None = None,
    diagnostic_limit: int = 3,
    max_record_bytes: int = 65_536,
) -> FileTaskIntentStore:
    return FileTaskIntentStore(
        root_directory=root,
        owner=runtime_owner() if owner is None else owner,
        diagnostic_limit=diagnostic_limit,
        max_record_bytes=max_record_bytes,
    )


def _source(tmp_path: Path, *, shuffle: bool = False) -> tuple[Path, dict[str, object]]:
    path = tmp_path / "tasks.jsonl"
    path.write_bytes(SOURCE_BYTES)
    return path, source_binding(path, shuffle=shuffle)


def _attach(parent: FileTaskIntentStore, *, max_source_bytes: int = 65_536) -> Any:
    assert _postings is not None
    return _postings.FileTaskPostingStore.attach(
        parent,
        max_source_bytes=max_source_bytes,
    )


def _bind(
    postings: Any,
    binding: dict[str, object],
    *,
    source_bytes: bytes = SOURCE_BYTES,
) -> dict[str, object]:
    return postings.bind_source(binding, source_bytes=source_bytes)


def _record_path(parent: FileTaskIntentStore, sequence: int) -> Path:
    return parent.identity_directory / "postings" / "records" / f"{sequence:020d}.json"


def _head_path(parent: FileTaskIntentStore) -> Path:
    return parent.identity_directory / "postings" / "head.json"


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _identity(number: int) -> tuple[str, str]:
    return (
        f"op-00000000-0000-4000-8000-{number:012d}",
        f"t-00000000-0000-4000-8000-{number + 1:012d}",
    )


def _reserve(postings: Any, number: int = 501) -> Any:
    operation_id, task_id = _identity(number)
    return postings.reserve(
        selection(),
        posting_intent(operation_id=operation_id, task_id=task_id),
    )


def _publish(postings: Any, number: int) -> Any:
    record = _reserve(postings, number)
    response = posting_response(record.intent, "published")
    return postings.record_observation(
        record.selection_sequence,
        record.revision,
        response,
        observation="submit",
    )


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _replace_json(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(canonical_bytes(value))
    path.chmod(0o600)


def _source_line(task: dict[str, object]) -> bytes:
    return canonical_bytes(task) + b"\n"


def _source_entry_id(
    binding: dict[str, object],
    *,
    index: int,
    line_number: int,
    line_sha256: str,
    task: dict[str, object],
) -> str:
    return canonical_sha256(
        {
            "snapshot_sha256": binding["source_snapshot_sha256"],
            "nonblank_entry_index": index,
            "physical_line_number": line_number,
            "exact_line_sha256": line_sha256,
            "raw_task_sha256": canonical_sha256(task),
        }
    )


def test_runtime_posting_store_interface_is_available() -> None:
    assert _postings is not None, (
        "MP-05 capability missing: base_agent.runtime_postings has not been implemented"
    )
    assert _codec is not None, (
        "MP-05 capability missing: service_auth.posting_journal has not been implemented"
    )
    assert _missing_exports == [], f"MP-05 posting interface is incomplete: {_missing_exports}"


def test_attach_borrows_parent_lease_and_child_close_does_not_close_parent(
    tmp_path: Path,
) -> None:
    parent = _intent_store(tmp_path)
    with parent:
        with _attach(parent) as postings:
            assert postings.head() is None
            with pytest.raises(RuntimeIntentError) as raised:
                with _intent_store(tmp_path):
                    pass
            assert raised.value.reason == "lease_busy"
        assert parent.unfinished() == ()
        with pytest.raises(RuntimeIntentError) as raised:
            postings.head()
        assert raised.value.reason == "invalid_input"


def test_posting_operations_fail_after_parent_context_closes(tmp_path: Path) -> None:
    parent = _intent_store(tmp_path)
    parent.__enter__()
    child = _attach(parent)
    postings = child.__enter__()
    parent.__exit__(None, None, None)
    try:
        with pytest.raises(RuntimeIntentError) as raised:
            postings.head()
        assert raised.value.reason in {"invalid_input", "unsafe_path", "storage_failure"}
    finally:
        child.__exit__(None, None, None)


@pytest.mark.parametrize("invalid_limit", [0, -1, True, 1.5])
def test_attach_requires_explicit_positive_integer_source_limit(
    tmp_path: Path,
    invalid_limit: object,
) -> None:
    with _intent_store(tmp_path) as parent:
        with pytest.raises(RuntimeIntentError) as raised:
            with _attach(parent, max_source_bytes=invalid_limit):
                pass
        assert raised.value.reason == "invalid_input"


def test_bind_source_persists_exact_genesis_head_and_returns_independent_json(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    owner = runtime_owner()
    owner_sha256 = canonical_sha256(owner.as_json())
    binding_sha256 = canonical_sha256(binding)
    genesis = canonical_sha256(
        {
            "schema_version": 1,
            "owner_sha256": owner_sha256,
            "source_binding_sha256": binding_sha256,
        }
    )
    with _intent_store(tmp_path / "runtime", owner=owner) as parent:
        with _attach(parent) as postings:
            head = _bind(postings, binding)
            assert set(head) == _HEAD_KEYS
            assert head == {
                "schema_version": 1,
                "revision": 0,
                "owner_sha256": owner_sha256,
                "source_binding": binding,
                "source_binding_sha256": binding_sha256,
                "selection_algorithm": "feeder-cycle-sha256-v1",
                "committed_selection_count": 0,
                "last_selection_id": None,
                "last_record_semantic_sha256": None,
                "chain_sha256": genesis,
            }
            head["revision"] = 999
            rebound = postings.head()
            assert rebound is not None and rebound["revision"] == 0
            binding["source_size_bytes"] = 999
            assert rebound["source_binding"] != binding
            head_path = _head_path(parent)
            source_snapshot = head_path.parent / "source.jsonl"
            assert head_path.read_bytes() == canonical_bytes(rebound)
            assert source_snapshot.read_bytes() == SOURCE_BYTES
            assert _mode(head_path) == 0o600
            assert _mode(source_snapshot) == 0o600
            assert source_snapshot.stat().st_nlink == 1
            assert _mode(head_path.parent) == 0o700
            assert _mode(head_path.parent / "records") == 0o700


def test_source_snapshot_precedes_initial_head_and_same_bytes_finish_interrupted_bind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    real_link = os.link

    def fail_head_publication(
        source: str,
        destination: str,
        *args: object,
        **kwargs: object,
    ) -> None:
        if str(destination) == "head.json":
            raise OSError("injected initial head publication failure")
        real_link(source, destination, *args, **kwargs)  # type: ignore[arg-type]

    with _intent_store(root) as parent, _attach(parent) as postings:
        with monkeypatch.context() as patch:
            patch.setattr(os, "link", fail_head_publication)
            with pytest.raises(RuntimeIntentError) as raised:
                _bind(postings, binding)
        assert raised.value.reason == "storage_failure"
        snapshot = parent.identity_directory / "postings" / "source.jsonl"
        assert snapshot.read_bytes() == SOURCE_BYTES
        assert not _head_path(parent).exists()
        assert postings.records() == ()
        with pytest.raises(RuntimeIntentError) as changed:
            _bind(postings, binding, source_bytes=SOURCE_BYTES + b"\n")
        assert changed.value.reason in {"source_conflict", "corrupt_record"}
        head = _bind(postings, binding)
        assert head["committed_selection_count"] == 0
        assert snapshot.read_bytes() == SOURCE_BYTES


@pytest.mark.parametrize("damage", ["missing", "changed", "invalid-utf8"])
def test_reopen_rejects_missing_or_corrupt_bound_source_snapshot(
    tmp_path: Path,
    damage: str,
) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent, _attach(parent) as postings:
        _bind(postings, binding)
        source_snapshot = parent.identity_directory / "postings" / "source.jsonl"
    if damage == "missing":
        source_snapshot.unlink()
    elif damage == "changed":
        source_snapshot.write_bytes(SOURCE_BYTES.replace(b"one", b"ONE"))
        source_snapshot.chmod(0o600)
    else:
        source_snapshot.write_bytes(b"\xff\n")
        source_snapshot.chmod(0o600)
    with pytest.raises(RuntimeIntentError) as raised, _intent_store(root) as parent:
        with _attach(parent):
            pass
    assert raised.value.reason == "corrupt_record"


def test_source_byte_limit_rejects_before_publication_and_when_reduced_on_reopen(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    fresh_root = tmp_path / "fresh-runtime"
    with (
        _intent_store(fresh_root) as parent,
        _attach(
            parent,
            max_source_bytes=len(SOURCE_BYTES) - 1,
        ) as postings,
    ):
        with pytest.raises(RuntimeIntentError) as raised:
            _bind(postings, binding)
        assert raised.value.reason == "record_too_large"
        posting_path = parent.identity_directory / "postings"
        assert not (posting_path / "source.jsonl").exists()
        assert not (posting_path / "head.json").exists()
    bound_root = tmp_path / "bound-runtime"
    with _intent_store(bound_root) as parent, _attach(parent) as postings:
        _bind(postings, binding)
    with pytest.raises(RuntimeIntentError) as reopened, _intent_store(bound_root) as parent:
        with _attach(parent, max_source_bytes=len(SOURCE_BYTES) - 1):
            pass
    assert reopened.value.reason == "record_too_large"


@pytest.mark.parametrize(
    "invalid_source",
    [b"", b" \n\r\n", b"not-json\n", b"[]\n", b"\xff\n"],
)
def test_initial_bind_rejects_empty_malformed_or_non_utf8_snapshot_without_artifacts(
    tmp_path: Path,
    invalid_source: bytes,
) -> None:
    _source_path, binding = _source(tmp_path)
    invalid_binding = copy.deepcopy(binding)
    invalid_binding["source_snapshot_sha256"] = hashlib.sha256(invalid_source).hexdigest()
    invalid_binding["source_size_bytes"] = len(invalid_source)
    with _intent_store(tmp_path / "runtime") as parent, _attach(parent) as postings:
        with pytest.raises(RuntimeIntentError) as raised:
            _bind(postings, invalid_binding, source_bytes=invalid_source)
        assert raised.value.reason == "invalid_input"
        posting_path = parent.identity_directory / "postings"
        assert not (posting_path / "source.jsonl").exists()
        assert not (posting_path / "head.json").exists()


def test_exact_source_at_relocated_path_preserves_original_binding_and_head_bytes(
    tmp_path: Path,
) -> None:
    source_path, binding = _source(tmp_path)
    relocated = tmp_path / "relocated" / "tasks.jsonl"
    relocated.parent.mkdir(mode=0o700)
    relocated.write_bytes(source_path.read_bytes())
    relocated_binding = source_binding(relocated)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent, _attach(parent) as postings:
        original = _bind(postings, binding)
        head_path = _head_path(parent)
        original_bytes = head_path.read_bytes()
        matched = _bind(postings, relocated_binding, source_bytes=relocated.read_bytes())
        assert matched == original
        assert matched["source_binding"] == binding
        assert matched["source_binding_sha256"] == canonical_sha256(binding)
        assert head_path.read_bytes() == original_bytes
        assert postings.records() == ()


def test_bind_source_is_exactly_idempotent_and_changed_binding_is_rejected(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    changed = copy.deepcopy(binding)
    terms = changed["posting_terms"]
    assert isinstance(terms, dict)
    terms["review_deadline_seconds"] = 91
    with _intent_store(tmp_path / "runtime") as parent:
        with _attach(parent) as postings:
            first = _bind(postings, binding)
            assert _bind(postings, copy.deepcopy(binding)) == first
            with pytest.raises(RuntimeIntentError) as raised:
                _bind(postings, changed)
            assert raised.value.reason == "source_conflict"
            assert postings.head() == first


def test_reserve_persists_exact_semantics_chain_and_head_before_returning(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    owner = runtime_owner()
    intent = posting_intent()
    with _intent_store(tmp_path / "runtime", owner=owner) as parent:
        with _attach(parent) as postings:
            initial_head = _bind(postings, binding)
            record = postings.reserve(selection(), intent)
            record_json = record.as_json()
            assert set(record_json) == _RECORD_KEYS
            semantic = {key: record_json[key] for key in _SEMANTIC_KEYS}
            semantic_sha256 = canonical_sha256(semantic)
            selection_chain_sha256 = canonical_sha256(
                {
                    "previous_chain_sha256": initial_head["chain_sha256"],
                    "semantic_sha256": semantic_sha256,
                }
            )
            assert record_json["schema_version"] == 1
            assert record_json["owner_sha256"] == initial_head["owner_sha256"]
            assert record_json["source_binding_sha256"] == initial_head["source_binding_sha256"]
            assert record_json["selection_sequence"] == 0
            assert record_json["cycle_index"] == 0
            assert record_json["cycle_position"] == 0
            assert record_json["source_entry_index"] == 0
            assert record_json["source_line_number"] == 1
            assert record_json["raw_task"] == raw_task()
            assert record_json["raw_task_sha256"] == canonical_sha256(raw_task())
            assert record_json["intent"] == intent.as_json()
            assert record_json["request_sha256"] == intent.request_sha256
            assert record_json["semantic_sha256"] == semantic_sha256
            assert record_json["previous_chain_sha256"] == initial_head["chain_sha256"]
            assert record_json["selection_chain_sha256"] == selection_chain_sha256
            assert record_json["revision"] == 0
            assert record_json["state"] == "prepared"
            assert record_json["observed_status_code"] is None
            assert record_json["publication"] is None
            assert record_json["diagnostics"] == []
            persisted = _record_path(parent, 0)
            assert persisted.read_bytes() == canonical_bytes(record_json)
            assert _mode(persisted) == 0o600
            assert persisted.stat().st_nlink == 1
            head = postings.head()
            assert head is not None
            assert head["revision"] == 1
            assert head["committed_selection_count"] == 1
            assert head["last_selection_id"] == record.selection_id
            assert head["last_record_semantic_sha256"] == semantic_sha256
            assert head["chain_sha256"] == selection_chain_sha256


def test_record_is_durable_before_head_replace_and_no_transport_runs_on_head_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    transport = Mock()
    with _intent_store(root) as parent, _attach(parent) as postings:
        initial_head = _bind(postings, binding)

        def fail_head_replace(
            _source: str,
            _destination: str,
            *,
            src_dir_fd: int,
            dst_dir_fd: int,
        ) -> None:
            del src_dir_fd, dst_dir_fd
            raise OSError("injected head replace failure")

        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", fail_head_replace)
            with pytest.raises(RuntimeIntentError) as raised:
                record = postings.reserve(selection(), posting_intent())
                transport(record.intent)
        assert raised.value.reason == "storage_failure"
        transport.assert_not_called()
        assert _record_path(parent, 0).is_file()
        assert postings.head() == initial_head
    with _intent_store(root) as parent, _attach(parent) as reopened:
        adopted = reopened.records()
        assert len(adopted) == 1
        assert adopted[0].intent == posting_intent()
        head = reopened.head()
        assert head is not None and head["committed_selection_count"] == 1


def test_reserve_rejects_wrong_owner_or_source_terms_without_writing(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    cases = (
        posting_intent(actor_id="a-foreign-poster"),
        posting_intent(title="Changed title"),
        posting_intent(spec="Changed specification"),
        posting_intent(reward=14),
        posting_intent(acceptance_window_seconds=31),
    )
    for index, intent in enumerate(cases):
        with _intent_store(tmp_path / f"runtime-{index}") as parent:
            with _attach(parent) as postings:
                before = _bind(postings, binding)
                with pytest.raises(RuntimeIntentError) as raised:
                    postings.reserve(selection(), intent)
                assert raised.value.reason == "invalid_input"
                assert postings.head() == before
                assert postings.records() == ()


def test_reserve_rejects_out_of_range_or_incoherent_source_evidence(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    invalid = (
        selection(source_entry_index=1),
        selection(source_entry_index=True),
        selection(source_line_number=0),
        {**selection(), "source_line_sha256": "0" * 64},
        {**selection(), "extra": "field"},
    )
    for index, candidate in enumerate(invalid):
        with _intent_store(tmp_path / f"runtime-{index}") as parent:
            with _attach(parent) as postings:
                before = _bind(postings, binding)
                with pytest.raises(RuntimeIntentError) as raised:
                    postings.reserve(candidate, posting_intent())
                assert raised.value.reason == "invalid_input"
                assert postings.head() == before
                assert postings.records() == ()


def test_reserve_rejects_self_consistent_selection_absent_from_persisted_catalog(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    forged_task = raw_task(solutions=["forged answer"])
    forged_line = _source_line(forged_task)
    forged_selection = {
        "source_entry_index": 0,
        "source_line_number": 1,
        "source_line_sha256": hashlib.sha256(forged_line).hexdigest(),
        "raw_task": forged_task,
    }
    with _intent_store(tmp_path / "runtime") as parent, _attach(parent) as postings:
        before = _bind(postings, binding)
        with pytest.raises(RuntimeIntentError) as raised:
            postings.reserve(forged_selection, posting_intent())
        assert raised.value.reason == "invalid_input"
        assert postings.head() == before
        assert postings.records() == ()


def test_unresolved_record_prevents_allocating_a_fresh_selection(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime") as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            first = _reserve(postings)
            with pytest.raises(RuntimeIntentError) as raised:
                _reserve(postings, 601)
            assert raised.value.reason == "unresolved_posting"
            assert postings.records() == (first,)


def test_record_before_head_crash_residue_is_adopted_with_exact_saved_identities(
    tmp_path: Path,
) -> None:
    source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            _publish(postings, 501)
            prior_head = _head_path(parent).read_bytes()
            successor = _reserve(postings, 601)
            successor_bytes = _record_path(parent, 1).read_bytes()
        _head_path(parent).write_bytes(prior_head)
        _head_path(parent).chmod(0o600)
    source_path.unlink()
    with _intent_store(root) as parent:
        with _attach(parent) as reopened:
            assert reopened.records()[1] == successor
            assert _record_path(parent, 1).read_bytes() == successor_bytes
            head = reopened.head()
            assert head is not None
            assert head["committed_selection_count"] == 2
            assert head["last_selection_id"] == successor.selection_id


def test_reopen_rejects_self_consistent_forged_successor_not_in_source_catalog(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent, _attach(parent) as postings:
        _bind(postings, binding)
        _publish(postings, 501)
        prior_head = _head_path(parent).read_bytes()
        successor = _reserve(postings, 601)
        successor_path = _record_path(parent, 1)
        forged = successor.as_json()
        forged_task = raw_task(solutions=["forged answer"])
        forged_line_sha256 = hashlib.sha256(_source_line(forged_task)).hexdigest()
        forged_entry_id = _source_entry_id(
            binding,
            index=0,
            line_number=1,
            line_sha256=forged_line_sha256,
            task=forged_task,
        )
        forged["source_line_sha256"] = forged_line_sha256
        forged["source_entry_id"] = forged_entry_id
        forged["raw_task"] = forged_task
        forged["raw_task_sha256"] = canonical_sha256(forged_task)
        forged["selection_id"] = canonical_sha256(
            {
                "cycle_id": forged["cycle_id"],
                "cycle_position": forged["cycle_position"],
                "source_entry_id": forged_entry_id,
            }
        )
        semantic = {key: forged[key] for key in _SEMANTIC_KEYS}
        semantic_sha256 = canonical_sha256(semantic)
        forged["semantic_sha256"] = semantic_sha256
        forged["selection_chain_sha256"] = canonical_sha256(
            {
                "previous_chain_sha256": forged["previous_chain_sha256"],
                "semantic_sha256": semantic_sha256,
            }
        )
        _replace_json(successor_path, forged)
        forged_bytes = successor_path.read_bytes()
        _head_path(parent).write_bytes(prior_head)
        _head_path(parent).chmod(0o600)
    with pytest.raises(RuntimeIntentError) as raised, _intent_store(root) as parent:
        with _attach(parent):
            pass
    assert raised.value.reason == "corrupt_record"
    assert _record_path(parent, 1).read_bytes() == forged_bytes


def test_reopen_rejects_head_referencing_missing_tail_without_repair(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            _reserve(postings)
            head_bytes = _head_path(parent).read_bytes()
            tail = _record_path(parent, 0)
        tail.unlink()
    with pytest.raises(RuntimeIntentError) as raised, _intent_store(root) as parent:
        with _attach(parent):
            pass
    assert raised.value.reason == "corrupt_record"
    assert _head_path(parent).read_bytes() == head_bytes
    assert not tail.exists()


def test_reopen_rejects_gap_in_committed_prefix_without_mutation(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            _publish(postings, 501)
            _publish(postings, 601)
            _publish(postings, 701)
            head_bytes = _head_path(parent).read_bytes()
            missing = _record_path(parent, 1)
        missing.unlink()
    with pytest.raises(RuntimeIntentError) as raised, _intent_store(root) as parent:
        with _attach(parent):
            pass
    assert raised.value.reason == "corrupt_record"
    assert _head_path(parent).read_bytes() == head_bytes
    assert not missing.exists()


def test_reopen_rejects_multiple_successors_beyond_head_without_adopting(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            _publish(postings, 501)
            one_record_head = _head_path(parent).read_bytes()
            _publish(postings, 601)
            _reserve(postings, 701)
            second_bytes = _record_path(parent, 1).read_bytes()
            third_bytes = _record_path(parent, 2).read_bytes()
        _head_path(parent).write_bytes(one_record_head)
        _head_path(parent).chmod(0o600)
    with pytest.raises(RuntimeIntentError) as raised, _intent_store(root) as parent:
        with _attach(parent):
            pass
    assert raised.value.reason == "corrupt_record"
    assert _head_path(parent).read_bytes() == one_record_head
    assert _record_path(parent, 1).read_bytes() == second_bytes
    assert _record_path(parent, 2).read_bytes() == third_bytes


@pytest.mark.parametrize("duplicate", ["task_id", "operation_id"])
def test_reopen_rejects_duplicate_native_identity_in_otherwise_valid_chain(
    tmp_path: Path,
    duplicate: str,
) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent, _attach(parent) as postings:
        _bind(postings, binding)
        first = _publish(postings, 501)
        second = _reserve(postings, 601)
        second_path = _record_path(parent, 1)
        head_path = _head_path(parent)
        second_json = second.as_json()
        replacement = posting_intent(
            operation_id=(
                first.intent.operation_id
                if duplicate == "operation_id"
                else second.intent.operation_id
            ),
            task_id=first.intent.task_id if duplicate == "task_id" else second.intent.task_id,
        )
        second_json["intent"] = replacement.as_json()
        second_json["request_sha256"] = replacement.request_sha256
        semantic = {key: second_json[key] for key in _SEMANTIC_KEYS}
        semantic_sha256 = canonical_sha256(semantic)
        chain_sha256 = canonical_sha256(
            {
                "previous_chain_sha256": second_json["previous_chain_sha256"],
                "semantic_sha256": semantic_sha256,
            }
        )
        second_json["semantic_sha256"] = semantic_sha256
        second_json["selection_chain_sha256"] = chain_sha256
        _replace_json(second_path, second_json)
        head = _load_json(head_path)
        head["last_record_semantic_sha256"] = semantic_sha256
        head["chain_sha256"] = chain_sha256
        _replace_json(head_path, head)
        second_bytes = second_path.read_bytes()
    with pytest.raises(RuntimeIntentError) as raised, _intent_store(root) as parent:
        with _attach(parent):
            pass
    assert raised.value.reason in {"operation_conflict", "corrupt_record"}
    assert _record_path(parent, 1).read_bytes() == second_bytes


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("owner_sha256", "0" * 64),
        ("source_binding_sha256", "1" * 64),
        ("previous_chain_sha256", "2" * 64),
        ("semantic_sha256", "3" * 64),
        ("selection_chain_sha256", "4" * 64),
    ],
)
def test_reopen_rejects_owner_source_or_chain_corruption_without_rewriting_record(
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    with _intent_store(root) as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            _reserve(postings)
            record_path = _record_path(parent, 0)
        damaged = _load_json(record_path)
        damaged[field] = replacement
        _replace_json(record_path, damaged)
        damaged_bytes = record_path.read_bytes()
    with pytest.raises(RuntimeIntentError) as raised, _intent_store(root) as parent:
        with _attach(parent):
            pass
    assert raised.value.reason == "corrupt_record"
    assert _record_path(parent, 0).read_bytes() == damaged_bytes


@pytest.mark.parametrize(
    "attack",
    [
        "head-mode",
        "record-hardlink",
        "head-symlink",
        "source-mode",
        "source-hardlink",
        "source-symlink",
    ],
)
def test_reopen_rejects_unsafe_record_modes_and_links(tmp_path: Path, attack: str) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    outside.chmod(0o600)
    with _intent_store(root) as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            _reserve(postings)
            head_path = _head_path(parent)
            record_path = _record_path(parent, 0)
            source_snapshot = head_path.parent / "source.jsonl"
        if attack == "head-mode":
            head_path.chmod(0o644)
        elif attack == "record-hardlink":
            os.link(record_path, tmp_path / "record-link.json")
        elif attack == "head-symlink":
            head_path.unlink()
            head_path.symlink_to(outside)
        elif attack == "source-mode":
            source_snapshot.chmod(0o644)
        elif attack == "source-hardlink":
            os.link(source_snapshot, tmp_path / "source-link.jsonl")
        else:
            source_snapshot.unlink()
            source_snapshot.symlink_to(outside)
    with pytest.raises(RuntimeIntentError) as raised, _intent_store(root) as parent:
        with _attach(parent):
            pass
    assert raised.value.reason in {"unsafe_mode", "unsafe_path"}


def test_parent_reopen_rejects_postings_directory_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    root = tmp_path / "runtime"
    outside = tmp_path / "outside-postings"
    outside.mkdir(mode=0o700)
    with _intent_store(root) as parent, _attach(parent) as postings:
        _bind(postings, binding)
        postings_path = parent.identity_directory / "postings"
    parked = tmp_path / "parked-postings"
    postings_path.rename(parked)
    postings_path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeIntentError) as raised:
        with _intent_store(root):
            pass
    assert raised.value.reason == "unsafe_path"
    assert list(outside.iterdir()) == []


def test_record_byte_limit_rejects_before_head_advance_or_transport(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    transport = Mock()
    selected = raw_task(spec="x" * 6_000)
    intent = posting_intent(spec="x" * 6_000)
    with _intent_store(tmp_path / "runtime", max_record_bytes=4_096) as parent:
        with _attach(parent) as postings:
            head = _bind(postings, binding)
            with pytest.raises(RuntimeIntentError) as raised:
                record = postings.reserve(selection(selected_task=selected), intent)
                transport(record.intent)
            assert raised.value.reason == "record_too_large"
            transport.assert_not_called()
            assert postings.head() == head
            assert postings.records() == ()


@pytest.mark.parametrize(
    ("state", "status_observation", "expected_state", "expected_status"),
    [
        ("awaiting_lock", False, "awaiting_lock", 202),
        ("published", False, "published", 201),
        ("published", True, "published", 200),
        ("blocked_integrity", False, "blocked_integrity", 409),
        ("blocked_integrity", True, "blocked_integrity", 200),
    ],
)
def test_record_observation_accepts_only_typed_sdk_ack_and_normalizes_publication(
    tmp_path: Path,
    state: str,
    status_observation: bool,
    expected_state: str,
    expected_status: int,
) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime") as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            record = _reserve(postings)
            head_bytes = _head_path(parent).read_bytes()
            response = posting_response(
                record.intent,
                state,  # type: ignore[arg-type]
                status_observation=status_observation,
            )
            updated = postings.record_observation(
                record.selection_sequence,
                record.revision,
                response,
                observation="status" if status_observation else "submit",
            )
            assert updated.revision == 1
            assert updated.state == expected_state
            assert updated.observed_status_code == expected_status
            assert updated.publication is not None
            assert updated.publication["publication_state"] == expected_state
            assert updated.publication["operation_id"] == record.intent.operation_id
            assert updated.publication["task_id"] == record.intent.task_id
            if state == "blocked_integrity" and not status_observation:
                assert updated.publication == response.body["details"]["publication"]
            else:
                assert updated.publication == response.body
            assert _head_path(parent).read_bytes() == head_bytes


def test_mismatched_or_malformed_typed_ack_never_replaces_saved_intent(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime") as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            record = _reserve(postings)
            foreign = posting_intent(
                operation_id="op-00000000-0000-4000-8000-000000000801",
                task_id="t-00000000-0000-4000-8000-000000000802",
            )
            foreign_response = posting_response(foreign, "published")
            malformed = TaskPostingResponse(
                status_code=201,
                body={**foreign_response.body, "operation_id": record.intent.operation_id},
            )
            for response in (foreign_response, malformed, object()):
                with pytest.raises(RuntimeIntentError) as raised:
                    postings.record_observation(
                        record.selection_sequence,
                        record.revision,
                        response,
                        observation="submit",
                    )
                assert raised.value.reason == "invalid_acknowledgment"
                assert postings.records() == (record,)


def test_stale_revision_is_rejected_without_overwriting_newer_observation(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime") as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            record = _reserve(postings)
            pending = postings.record_observation(
                record.selection_sequence,
                record.revision,
                posting_response(record.intent, "awaiting_lock"),
                observation="submit",
            )
            with pytest.raises(RuntimeIntentError) as raised:
                postings.record_diagnostic(
                    record.selection_sequence,
                    record.revision,
                    code="transport_error",
                    status_code=None,
                    attention=False,
                )
            assert raised.value.reason == "stale_revision"
            assert postings.records() == (pending,)


def test_published_or_blocked_record_cannot_regress_or_become_retryable(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    for index, terminal_state in enumerate(("published", "blocked_integrity")):
        with _intent_store(tmp_path / f"runtime-{index}") as parent:
            with _attach(parent) as postings:
                _bind(postings, binding)
                record = _reserve(postings)
                terminal = postings.record_observation(
                    record.selection_sequence,
                    record.revision,
                    posting_response(record.intent, terminal_state),  # type: ignore[arg-type]
                    observation="submit",
                )
                diagnostic = postings.record_diagnostic(
                    terminal.selection_sequence,
                    terminal.revision,
                    code="transport_error",
                    status_code=None,
                    attention=False,
                )
                assert diagnostic.state == terminal_state
                assert diagnostic.publication == terminal.publication
                replay = postings.record_observation(
                    diagnostic.selection_sequence,
                    diagnostic.revision,
                    posting_response(record.intent, terminal_state),  # type: ignore[arg-type]
                    observation="submit",
                )
                assert replay == diagnostic
                with pytest.raises(RuntimeIntentError) as raised:
                    postings.record_observation(
                        replay.selection_sequence,
                        replay.revision,
                        posting_response(record.intent, "awaiting_lock"),
                        observation="status",
                    )
                assert raised.value.reason == "invalid_acknowledgment"
                assert postings.records() == (diagnostic,)


def test_diagnostics_are_bounded_strict_and_attention_is_explicit(tmp_path: Path) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime", diagnostic_limit=2) as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            record = _reserve(postings)
            first = postings.record_diagnostic(
                record.selection_sequence,
                record.revision,
                code="transport_error",
                status_code=None,
                attention=False,
            )
            assert first.state == "prepared"
            second = postings.record_diagnostic(
                first.selection_sequence,
                first.revision,
                code="http_rejection",
                status_code=503,
                attention=False,
            )
            assert second.state == "prepared"
            third = postings.record_diagnostic(
                second.selection_sequence,
                second.revision,
                code="invalid_acknowledgment",
                status_code=502,
                attention=True,
            )
            assert len(third.diagnostics) == 2
            assert [item["code"] for item in third.diagnostics] == [
                "http_rejection",
                "invalid_acknowledgment",
            ]
            for bad in (
                {"code": "secret-token-body", "status_code": None, "attention": True},
                {"code": "transport_error", "status_code": True, "attention": True},
                {"code": "transport_error", "status_code": 200, "attention": 1},
            ):
                with pytest.raises(RuntimeIntentError) as raised:
                    postings.record_diagnostic(
                        third.selection_sequence,
                        third.revision,
                        **bad,
                    )
                assert raised.value.reason == "invalid_input"


@pytest.mark.parametrize(
    ("code", "status_code", "attention", "expected_state"),
    [
        ("transport_error", None, False, "prepared"),
        ("invalid_acknowledgment", None, True, "attention_required"),
        ("invalid_acknowledgment", 502, True, "attention_required"),
        ("http_rejection", 400, True, "attention_required"),
        ("http_rejection", 499, True, "attention_required"),
        ("http_rejection", 500, False, "prepared"),
        ("http_rejection", 599, False, "prepared"),
    ],
)
def test_diagnostic_flag_mapping_is_exact(
    tmp_path: Path,
    code: str,
    status_code: int | None,
    attention: bool,
    expected_state: str,
) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime") as parent, _attach(parent) as postings:
        _bind(postings, binding)
        record = _reserve(postings)
        updated = postings.record_diagnostic(
            record.selection_sequence,
            record.revision,
            code=code,
            status_code=status_code,
            attention=attention,
        )
        assert updated.state == expected_state
        assert updated.observed_status_code is None
        assert updated.publication is None
        assert updated.diagnostics[-1] == {
            "code": code,
            "status_code": status_code,
            "attention": attention,
        }


@pytest.mark.parametrize(
    ("code", "status_code", "attention"),
    [
        ("transport_error", 503, False),
        ("transport_error", None, True),
        ("invalid_acknowledgment", None, False),
        ("http_rejection", None, True),
        ("http_rejection", 399, True),
        ("http_rejection", 600, False),
        ("http_rejection", 400, False),
        ("http_rejection", 503, True),
        ("integrity_blocked", 200, True),
    ],
)
def test_contradictory_diagnostic_arguments_fail_without_writes(
    tmp_path: Path,
    code: str,
    status_code: int | None,
    attention: bool,
) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime") as parent, _attach(parent) as postings:
        _bind(postings, binding)
        record = _reserve(postings)
        with pytest.raises(RuntimeIntentError) as raised:
            postings.record_diagnostic(
                record.selection_sequence,
                record.revision,
                code=code,
                status_code=status_code,
                attention=attention,
            )
        assert raised.value.reason == "invalid_input"
        assert postings.records() == (record,)


def test_attention_record_accepts_status_only_recovery_and_rejects_submit_clear(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    for index, recovered_state in enumerate(("awaiting_lock", "published")):
        with _intent_store(tmp_path / f"runtime-{index}") as parent, _attach(parent) as postings:
            _bind(postings, binding)
            record = _reserve(postings)
            attention = postings.record_diagnostic(
                record.selection_sequence,
                record.revision,
                code="invalid_acknowledgment",
                status_code=None,
                attention=True,
            )
            with pytest.raises(RuntimeIntentError) as raised:
                postings.record_observation(
                    attention.selection_sequence,
                    attention.revision,
                    posting_response(record.intent, recovered_state),  # type: ignore[arg-type]
                    observation="submit",
                )
            assert raised.value.reason == "invalid_acknowledgment"
            recovered = postings.record_observation(
                attention.selection_sequence,
                attention.revision,
                posting_response(
                    record.intent,
                    recovered_state,  # type: ignore[arg-type]
                    status_observation=True,
                ),
                observation="status",
            )
            assert recovered.state == recovered_state


def test_integrity_blocked_diagnostic_requires_frozen_blocked_envelope(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime") as parent, _attach(parent) as postings:
        _bind(postings, binding)
        record = _reserve(postings)
        blocked = postings.record_observation(
            record.selection_sequence,
            record.revision,
            posting_response(record.intent, "blocked_integrity"),
            observation="submit",
        )
        diagnosed = postings.record_diagnostic(
            blocked.selection_sequence,
            blocked.revision,
            code="integrity_blocked",
            status_code=409,
            attention=True,
        )
        assert diagnosed.state == "blocked_integrity"
        assert diagnosed.publication == blocked.publication
        assert diagnosed.observed_status_code == blocked.observed_status_code


def test_frozen_record_and_all_returned_json_are_deeply_immutable_or_independent(
    tmp_path: Path,
) -> None:
    _source_path, binding = _source(tmp_path)
    with _intent_store(tmp_path / "runtime") as parent:
        with _attach(parent) as postings:
            _bind(postings, binding)
            record = _reserve(postings)
            with pytest.raises(TypeError):
                record.raw_task["title"] = "tampered"
            solutions = record.raw_task["solutions"]
            assert isinstance(solutions, tuple)
            with pytest.raises(TypeError):
                solutions[0] = "tampered"
            first = record.as_json()
            raw = first["raw_task"]
            intent = first["intent"]
            assert isinstance(raw, dict) and isinstance(intent, dict)
            raw["title"] = "tampered"
            intent["reward"] = 999
            second = record.as_json()
            assert second["raw_task"] == raw_task()
            assert second["intent"] == record.intent.as_json()
            listed = postings.records()
            assert isinstance(listed, tuple) and listed == (record,)
