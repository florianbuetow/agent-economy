"""Reviewer-confirmed MP-05 posting journal regression cases."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from service_auth.posting_journal import (
    PostingJournalError,
    RawTask,
    audit_posting_ledger,
)

from base_agent import runtime_postings
from base_agent.runtime_intents import FileTaskIntentStore, RuntimeIntentError
from tests.fakes.mp05_posting_fixtures import (
    SOURCE_BYTES,
    canonical_bytes,
    canonical_sha256,
    posting_intent,
    raw_task,
    runtime_owner,
    selection,
    source_binding,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_raw_task_accepts_ordered_solution_array() -> None:
    valid = RawTask.from_json(raw_task(solutions=["first", "second"]))
    assert valid.solutions == ("first", "second")


@pytest.mark.parametrize("solutions", ["ab", {"answer": "a"}], ids=["string", "mapping"])
def test_raw_task_rejects_non_array_solutions(solutions: object) -> None:
    with pytest.raises(PostingJournalError):
        RawTask.from_json(raw_task(solutions=solutions))


def _semantic_keys() -> tuple[str, ...]:
    return (
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
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cycle_index", False),
        ("cycle_position", False),
        ("source_entry_index", False),
        ("source_line_number", True),
        ("solution_note", "missing"),
    ],
    ids=[
        "bool-cycle-index",
        "bool-cycle-position",
        "bool-entry-index",
        "bool-line-number",
        "missing-solution-note",
    ],
)
def test_audit_and_reopen_reject_self_consistent_forgery(
    tmp_path: Path, field: str, value: object
) -> None:
    owner = runtime_owner()
    root = tmp_path / "runtime"
    source_path = tmp_path / "tasks.jsonl"
    source_path.write_bytes(SOURCE_BYTES)
    binding = source_binding(source_path)
    with FileTaskIntentStore(
        root_directory=root, owner=owner, diagnostic_limit=3, max_record_bytes=65_536
    ) as parent:
        with runtime_postings.FileTaskPostingStore.attach(parent, max_source_bytes=65_536) as store:
            store.bind_source(binding, source_bytes=SOURCE_BYTES)
            store.reserve(selection(), posting_intent())
        directory = parent.identity_directory / "postings"
        head = json.loads((directory / "head.json").read_bytes())
        record = json.loads((directory / "records" / "00000000000000000000.json").read_bytes())
        record_bytes_original = canonical_bytes(record)
        owner_json = owner.as_json()
        source_bytes = (directory / "source.jsonl").read_bytes()
        head_bytes = canonical_bytes(head)

    changed = json.loads(record_bytes_original)
    if field == "solution_note":
        del changed["raw_task"]["solution_note"]
    else:
        changed[field] = value
    changed["semantic_sha256"] = canonical_sha256({key: changed[key] for key in _semantic_keys()})
    changed["selection_chain_sha256"] = canonical_sha256(
        {
            "previous_chain_sha256": changed["previous_chain_sha256"],
            "semantic_sha256": changed["semantic_sha256"],
        }
    )
    changed_head = json.loads(head_bytes)
    changed_head["last_record_semantic_sha256"] = changed["semantic_sha256"]
    changed_head["chain_sha256"] = changed["selection_chain_sha256"]
    changed_head_bytes = canonical_bytes(changed_head)
    changed_record_bytes = canonical_bytes(changed)

    result = audit_posting_ledger(
        owner_json=owner_json,
        source_bytes=source_bytes,
        head_bytes=changed_head_bytes,
        record_bytes={"00000000000000000000.json": changed_record_bytes},
        max_source_bytes=65_536,
        max_record_bytes=65_536,
        diagnostic_limit=3,
    )
    record_path = (
        root
        / "identities"
        / owner.key_fingerprint
        / "postings"
        / "records"
        / ("00000000000000000000.json")
    )
    head_path = record_path.parent.parent / "head.json"
    record_path.write_bytes(changed_record_bytes)
    head_path.write_bytes(changed_head_bytes)
    before_reopen = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    with (
        pytest.raises(RuntimeIntentError),
        FileTaskIntentStore(
            root_directory=root,
            owner=owner,
            diagnostic_limit=3,
            max_record_bytes=65_536,
        ) as parent,
        runtime_postings.FileTaskPostingStore.attach(parent, max_source_bytes=65_536),
    ):
        pass
    after_reopen = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert after_reopen == before_reopen
    assert result.state == "invalid"
    assert record_bytes_original == canonical_bytes(record)


def test_bind_source_rejects_oversize_head_before_publishing(tmp_path: Path) -> None:
    owner = runtime_owner()
    root = tmp_path / "runtime"
    source_path = tmp_path / "tasks.jsonl"
    source_path.write_bytes(SOURCE_BYTES)
    binding = source_binding(source_path)
    with FileTaskIntentStore(
        root_directory=root, owner=owner, diagnostic_limit=3, max_record_bytes=600
    ) as parent:
        before = set(parent.identity_directory.rglob("*"))
        with runtime_postings.FileTaskPostingStore.attach(parent, max_source_bytes=65_536) as store:
            snapshot = set(parent.identity_directory.rglob("*"))
            with pytest.raises(RuntimeIntentError) as raised:
                store.bind_source(binding, source_bytes=SOURCE_BYTES)
        assert raised.value.reason == "record_too_large"
        assert set(parent.identity_directory.rglob("*")) == snapshot
        assert before <= snapshot
        assert not list(parent.identity_directory.rglob("source.jsonl"))
        assert not list(parent.identity_directory.rglob("head.json"))
