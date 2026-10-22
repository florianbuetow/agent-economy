"""MP-05 single-link admission regressions for the existing MP-07 journal."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from base_agent.runtime_intent_files import open_root_directory, read_record_bytes
from base_agent.runtime_intents import (
    FileTaskIntentStore,
    NewTaskIntent,
    RuntimeIntentError,
    RuntimeOwner,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_TASK_ID = "t-00000000-0000-4000-8000-000000000591"
_OPERATION_ID = "op-00000000-0000-4000-8000-000000000591"


def _owner() -> RuntimeOwner:
    return RuntimeOwner(
        schema_version=1,
        environment_id="mp05-link-tests",
        role="math-worker",
        profile="link-worker",
        handle="link-worker",
        registered_agent_id="a-link-worker",
        key_fingerprint="9" * 64,
        service_urls={
            "identity_url": "https://identity.example",
            "bank_url": "https://bank.example",
            "task_board_url": "https://task-board.example",
            "reputation_url": "https://reputation.example",
            "court_url": "https://court.example",
        },
    )


def _intent() -> NewTaskIntent:
    return NewTaskIntent(
        task_id=_TASK_ID,
        slot="bid",
        action="submit_bid",
        source_status="open",
        target_status="open",
        operation_id=_OPERATION_ID,
        expected_lifecycle_version=4,
        public_arguments={
            "task_id": _TASK_ID,
            "amount": 11,
            "operation_id": _OPERATION_ID,
            "expected_lifecycle_version": 4,
        },
        acknowledgment_context={
            "response_kind": "bid",
            "actor_id": "a-link-worker",
            "operation_id": _OPERATION_ID,
            "task_id": _TASK_ID,
            "command": "submit_bid",
            "source_status": "open",
            "source_lifecycle_version": 4,
            "target_status": "open",
            "target_lifecycle_version": 4,
            "result_facts": {
                "bid_id": f"bid-{_OPERATION_ID[3:]}",
                "bidder_id": "a-link-worker",
                "amount": 11,
            },
            "frozen_task_facts": {"bidder_id": "a-link-worker"},
        },
    )


def _store(root: Path) -> FileTaskIntentStore:
    return FileTaskIntentStore(
        root_directory=root,
        owner=_owner(),
        diagnostic_limit=4,
        max_record_bytes=65_536,
    )


def test_pinned_record_read_rejects_a_multiply_linked_regular_file(tmp_path: Path) -> None:
    record = tmp_path / "record.json"
    alias = tmp_path / "outside-alias.json"
    record.write_bytes(b"{}")
    record.chmod(0o600)
    os.link(record, alias)
    directory = open_root_directory(tmp_path)
    try:
        with pytest.raises(RuntimeIntentError) as raised:
            read_record_bytes(directory, record.name, max_record_bytes=64)
        assert raised.value.reason == "unsafe_path"
    finally:
        directory.close()
    assert record.read_bytes() == alias.read_bytes() == b"{}"
    assert record.stat().st_nlink == 2


def test_reopen_rejects_multiply_linked_owner_record_without_rebinding(
    tmp_path: Path,
) -> None:
    with _store(tmp_path) as store:
        owner_path = store.owner_path
        owner_bytes = owner_path.read_bytes()
    alias = tmp_path / "owner-alias.json"
    os.link(owner_path, alias)

    with pytest.raises(RuntimeIntentError) as raised, _store(tmp_path):
        pass

    assert raised.value.reason == "unsafe_path"
    assert owner_path.read_bytes() == alias.read_bytes() == owner_bytes
    assert owner_path.stat().st_nlink == 2


def test_reopen_rejects_multiply_linked_intent_record_without_loading_it(
    tmp_path: Path,
) -> None:
    with _store(tmp_path) as store:
        store.create(_intent())
        record_path = store.intent_path(_TASK_ID, "bid")
        record_bytes = record_path.read_bytes()
    alias = tmp_path / "intent-alias.json"
    os.link(record_path, alias)

    with pytest.raises(RuntimeIntentError) as raised, _store(tmp_path):
        pass

    assert raised.value.reason == "unsafe_path"
    assert record_path.read_bytes() == alias.read_bytes() == record_bytes
    assert record_path.stat().st_nlink == 2


@pytest.mark.parametrize("record_kind", ["owner", "intent"])
def test_reopen_recovers_owned_link_publication_staging_alias_before_final_admission(
    tmp_path: Path,
    record_kind: str,
) -> None:
    with _store(tmp_path) as store:
        if record_kind == "owner":
            final_path = store.owner_path
            staging = final_path.parent / f".owner.json.{'a' * 24}.tmp"
        else:
            store.create(_intent())
            final_path = store.intent_path(_TASK_ID, "bid")
            staging = final_path.parent / f".{final_path.name}.{'b' * 24}.tmp"
        original = final_path.read_bytes()
    os.link(final_path, staging)
    assert final_path.stat().st_nlink == 2

    with _store(tmp_path) as reopened:
        if record_kind == "intent":
            assert reopened.load(_TASK_ID, "bid") is not None

    assert not staging.exists()
    assert final_path.read_bytes() == original
    assert final_path.stat().st_nlink == 1
