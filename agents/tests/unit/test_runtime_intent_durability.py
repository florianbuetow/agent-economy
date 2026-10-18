"""Runtime journal publication retains its lease and durable directory ancestry."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from base_agent.runtime_intents import (
    FileTaskIntentStore,
    NewTaskIntent,
    RuntimeIntentError,
    RuntimeOwner,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

TASK_ID = "t-00000000-0000-4000-8000-000000000912"
OPERATION_ID = "op-00000000-0000-4000-8000-000000000912"
ACTOR_ID = "a-durability-worker"


def _store(root: Path, max_record_bytes: int) -> FileTaskIntentStore:
    return FileTaskIntentStore(
        root_directory=root,
        owner=RuntimeOwner(
            schema_version=1,
            environment_id="durability-proof",
            role="math-worker",
            profile="durability-worker",
            handle="durability-worker",
            registered_agent_id=ACTOR_ID,
            key_fingerprint="b" * 64,
            service_urls={
                "identity_url": "https://identity.example",
                "bank_url": "https://bank.example",
                "task_board_url": "https://board.example",
                "reputation_url": "https://reputation.example",
                "court_url": "https://court.example",
            },
        ),
        diagnostic_limit=8,
        max_record_bytes=max_record_bytes,
    )


def _bid() -> NewTaskIntent:
    return NewTaskIntent(
        task_id=TASK_ID,
        slot="bid",
        action="submit_bid",
        source_status="open",
        target_status="open",
        operation_id=OPERATION_ID,
        expected_lifecycle_version=7,
        public_arguments={
            "task_id": TASK_ID,
            "amount": 9,
            "operation_id": OPERATION_ID,
            "expected_lifecycle_version": 7,
        },
        acknowledgment_context={
            "response_kind": "bid",
            "actor_id": ACTOR_ID,
            "operation_id": OPERATION_ID,
            "task_id": TASK_ID,
            "command": "submit_bid",
            "source_status": "open",
            "source_lifecycle_version": 7,
            "target_status": "open",
            "target_lifecycle_version": 7,
            "result_facts": {
                "bid_id": f"bid-{OPERATION_ID[3:]}",
                "bidder_id": ACTOR_ID,
                "amount": 9,
            },
            "frozen_task_facts": {"bidder_id": ACTOR_ID},
        },
    )


def test_replaced_lease_is_rejected_before_intent_publication(tmp_path: Path) -> None:
    transport = Mock()
    with _store(tmp_path, 65536) as store:
        parked = store.lock_path.with_name("held-original.lock")
        store.lock_path.rename(parked)
        store.lock_path.touch(mode=0o600)
        try:
            with pytest.raises(RuntimeIntentError):
                intent = store.create(_bid())
                transport(intent.public_arguments)
            transport.assert_not_called()
            assert not store.intent_path(TASK_ID, "bid").exists()
        finally:
            store.lock_path.unlink()
            parked.rename(store.lock_path)


def test_owner_publication_obeys_the_explicit_record_size_limit(tmp_path: Path) -> None:
    transport = Mock()
    store = _store(tmp_path, 1)
    with pytest.raises(RuntimeIntentError) as raised, store:
        transport()
    assert raised.value.reason == "record_too_large"
    assert not store.owner_path.exists()
    transport.assert_not_called()


def test_new_directory_parent_fsync_failure_prevents_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = Mock()
    real_fsync = os.fsync
    parent_stat = tmp_path.stat()
    parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
    observed = False

    def fail_parent_fsync(descriptor: int) -> None:
        nonlocal observed
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) == parent_identity:
            observed = True
            raise OSError("injected new journal parent durability failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_parent_fsync)
    with pytest.raises(RuntimeIntentError) as raised, _store(tmp_path / "new-journal", 65536):
        transport()
    assert raised.value.reason == "storage_failure"
    assert observed
    transport.assert_not_called()
