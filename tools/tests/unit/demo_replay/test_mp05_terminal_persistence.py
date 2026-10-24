"""Terminal demo acknowledgements are reported only after durable exact readback."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse

from demo_replay import run_store as run_store_module
from demo_replay.run_store import (
    ActorDefinition,
    RunEnvironment,
    RunStore,
    RunStoreError,
    StoredStep,
)
from demo_replay.run_store_files import RunStoreFileError, SafeDirectory

pytestmark = pytest.mark.unit

_RUN_ID = "run-00000000-0000-4000-8000-000000000581"
_AGENT_ID = "a-poster"
_TASK_ID = "t-00000000-0000-4000-8000-000000000582"
_ESCROW_ID = "esc-00000000-0000-4000-8000-000000000583"
_FUNDING_REFERENCE = "terminal-persistence"
_POSTING_STEP: dict[str, object] = {
    "action": "post_task",
    "poster": "poster",
    "title": "Durable terminal task",
    "spec": "Publish one exact acknowledgement.",
    "reward": 10,
    "bidding_deadline_seconds": 60,
    "acceptance_window_seconds": 30,
    "execution_deadline_seconds": 120,
    "review_deadline_seconds": 90,
}
_FUNDING_STEP: dict[str, object] = {
    "action": "fund",
    "agent": "poster",
    "amount": 50,
    "reference": _FUNDING_REFERENCE,
}
_SCENARIO: dict[str, object] = {
    "name": "terminal persistence",
    "agents": [{"handle": "poster", "name": "Poster"}],
    "steps": [_POSTING_STEP, _FUNDING_STEP],
}
_SCENARIO_BYTES = yaml.safe_dump(_SCENARIO, sort_keys=False).encode()


def _arguments(state_directory: Path) -> dict[str, Any]:
    return {
        "state_directory": state_directory.absolute(),
        "run_id": _RUN_ID,
        "environment": RunEnvironment(
            environment_id="terminal-persistence",
            identity_url="https://identity.invalid",
            bank_url="https://bank.invalid",
            task_board_url="https://board.invalid",
            reputation_url="https://reputation.invalid",
            court_url="https://court.invalid",
            platform_public_fingerprint_sha256="a" * 64,
        ),
        "scenario_bytes": _SCENARIO_BYTES,
        "scenario": _SCENARIO,
        "actor_definitions": (ActorDefinition(handle="poster", name="Poster"),),
        "effective_delay_seconds": 0.0,
        "max_record_bytes": 65_536,
        "diagnostic_limit": 2_048,
    }


def _bind(store: RunStore) -> bytes:
    actor = store.actors["poster"]
    private_key = actor.private_key_path.read_bytes()
    store.record_registration(
        actor_handle="poster",
        response={
            "agent_id": _AGENT_ID,
            "name": actor.name,
            "public_key": actor.public_key,
            "registered_at": "2026-10-22T09:59:00Z",
        },
    )
    return private_key


def _posting_intent() -> TaskPostingIntent:
    return TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id_for("demo-posting", (_TASK_ID, _AGENT_ID)),
            "task_id": _TASK_ID,
            "actor_kind": "agent",
            "actor_id": _AGENT_ID,
            "lock_operation_id": operation_id_for("task-lock", (_TASK_ID, _AGENT_ID)),
            "title": _POSTING_STEP["title"],
            "spec": _POSTING_STEP["spec"],
            "reward": _POSTING_STEP["reward"],
            "bidding_deadline_seconds": _POSTING_STEP["bidding_deadline_seconds"],
            "acceptance_window_seconds": _POSTING_STEP["acceptance_window_seconds"],
            "execution_deadline_seconds": _POSTING_STEP["execution_deadline_seconds"],
            "review_deadline_seconds": _POSTING_STEP["review_deadline_seconds"],
        }
    )


def _published_response(intent: TaskPostingIntent) -> TaskPostingResponse:
    published = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
    bidding = published + timedelta(seconds=intent.bidding_deadline_seconds)
    acceptance = bidding + timedelta(seconds=intent.acceptance_window_seconds)

    def timestamp(value: datetime) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def epoch_microseconds(value: datetime) -> int:
        return int(value.timestamp() * 1_000_000)

    result = {
        "task_id": intent.task_id,
        "poster_id": intent.actor_id,
        "title": intent.title,
        "spec": intent.spec,
        "reward": intent.reward,
        "bidding_deadline_seconds": intent.bidding_deadline_seconds,
        "acceptance_window_seconds": intent.acceptance_window_seconds,
        "execution_deadline_seconds": intent.execution_deadline_seconds,
        "review_deadline_seconds": intent.review_deadline_seconds,
        "status": "open",
        "escrow_id": _ESCROW_ID,
        "created_at": timestamp(published),
        "bidding_deadline": timestamp(bidding),
        "acceptance_deadline": timestamp(acceptance),
        "bidding_cutoff_us": epoch_microseconds(bidding),
        "acceptance_cutoff_us": epoch_microseconds(acceptance),
        "lifecycle_version": 0,
        "timing_contract_version": 2,
        "event_id": 91,
    }
    return TaskPostingResponse(
        status_code=201,
        body={
            "protocol_version": 1,
            "operation_id": intent.operation_id,
            "task_id": intent.task_id,
            "request_sha256": intent.request_sha256,
            "publication_state": "published",
            "lock_operation_id": intent.lock_operation_id,
            "escrow_id": _ESCROW_ID,
            "admitted_at": "2026-10-22T09:59:59.000000Z",
            "published_at": timestamp(published),
            "result": result,
            "error": None,
        },
    )


def _funding_intent() -> dict[str, object]:
    return {
        "operation_id": operation_id_for("demo-funding", (_AGENT_ID, _FUNDING_REFERENCE)),
        "account_id": _AGENT_ID,
        "amount": _FUNDING_STEP["amount"],
        "reference": _FUNDING_STEP["reference"],
    }


def _funding_result(intent: dict[str, object]) -> dict[str, object]:
    request = {
        "request_version": 1,
        "operation_kind": "credit",
        "account_id": intent["account_id"],
        "amount": intent["amount"],
        "reference": intent["reference"],
    }
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": intent["operation_id"],
            "operation_kind": "credit",
            "request_sha256": hashlib.sha256(encoded).hexdigest(),
            "source_reference": intent["reference"],
            "committed_at": "2026-10-22T10:00:00Z",
            "event_id": 92,
            "escrow_id": None,
            "result": {
                "account_id": intent["account_id"],
                "amount": intent["amount"],
                "balance_after": intent["amount"],
                "reference": intent["reference"],
            },
            "legs": [
                {
                    "role": "account",
                    "account_id": intent["account_id"],
                    "direction": "credit",
                    "amount": intent["amount"],
                    "transaction_id": "tx-terminal-persistence",
                    "balance_after": intent["amount"],
                }
            ],
        },
        "current_escrow": None,
    }


def _reserve_posting(store: RunStore) -> tuple[StoredStep, TaskPostingIntent]:
    intent = _posting_intent()
    prepared = store.reserve_posting(
        index=0,
        step=_POSTING_STEP,
        actor_handle="poster",
        intent=intent,
    )
    return store.mark_dispatching(index=0, expected_revision=prepared.revision), intent


def _reserve_funding(store: RunStore) -> tuple[StoredStep, dict[str, object]]:
    intent = _funding_intent()
    prepared = store.reserve_step(
        index=1,
        step=_FUNDING_STEP,
        actor_handle="poster",
        intent=intent,
    )
    return store.mark_dispatching(index=1, expected_revision=prepared.revision), intent


def test_posting_failure_before_replacement_keeps_original_pending_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        private_key = _bind(store)
        dispatched, intent = _reserve_posting(store)
        record = store.run_directory / "steps" / "000000.json"
        pending_bytes = record.read_bytes()

        def fail_before_replacement(*_args: object, **_kwargs: object) -> None:
            raise RunStoreFileError("storage_failure", "injected pre-replacement failure")

        monkeypatch.setattr(run_store_module, "replace_record", fail_before_replacement)
        with pytest.raises(RunStoreError) as raised:
            store.complete_posting(
                index=0,
                expected_revision=dispatched.revision,
                response=_published_response(intent),
            )

        assert raised.value.reason == "storage_failure"
        assert record.read_bytes() == pending_bytes

    with RunStore.open(**arguments) as reopened:
        restored = reopened.step(0)
        assert restored is not None
        assert restored.state == "dispatching"
        assert restored.intent == intent
        assert reopened.actors["poster"].private_key_path.read_bytes() == private_key


def test_financial_directory_fsync_failure_after_replacement_never_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _arguments(tmp_path / "state")
    real_fsync = os.fsync

    with RunStore.open(**arguments) as store:
        private_key = _bind(store)
        dispatched, intent = _reserve_funding(store)
        result = _funding_result(intent)
        steps_directory = store.run_directory / "steps"
        record = steps_directory / "000001.json"
        pending_inode = record.stat().st_ino

        def fail_post_replace_directory_fsync(descriptor: int) -> None:
            descriptor_stat = os.fstat(descriptor)
            steps_stat = steps_directory.stat()
            replacement_finished = record.stat().st_ino != pending_inode
            if replacement_finished and (descriptor_stat.st_dev, descriptor_stat.st_ino) == (
                steps_stat.st_dev,
                steps_stat.st_ino,
            ):
                raise OSError("injected post-replacement directory fsync failure")
            real_fsync(descriptor)

        monkeypatch.setattr(os, "fsync", fail_post_replace_directory_fsync)
        with pytest.raises(RunStoreError) as raised:
            store.finish_step(
                index=1,
                expected_revision=dispatched.revision,
                state="completed",
                result=result,
                diagnostic=None,
            )

        assert raised.value.reason == "storage_failure"

    monkeypatch.setattr(os, "fsync", real_fsync)
    with RunStore.open(**arguments) as reopened:
        restored = reopened.step(1)
        assert restored is not None
        assert restored.state == "completed"
        assert restored.intent == intent
        assert restored.result == result
        assert reopened.actors["poster"].private_key_path.read_bytes() == private_key


def test_failed_terminal_readback_raises_while_exact_persisted_acknowledgement_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments = _arguments(tmp_path / "state")
    real_replace = run_store_module.replace_record
    real_read_record = run_store_module.read_record
    replacement_finished = False

    with RunStore.open(**arguments) as store:
        private_key = _bind(store)
        dispatched, intent = _reserve_posting(store)
        record = store.run_directory / "steps" / "000000.json"
        pending_bytes = record.read_bytes()
        expected = _published_response(intent)

        def observe_replace(directory: SafeDirectory, name: str, content: bytes) -> None:
            nonlocal replacement_finished
            real_replace(directory, name, content)
            replacement_finished = True

        def stale_terminal_readback(
            directory: SafeDirectory,
            name: str,
            *,
            max_record_bytes: int,
        ) -> bytes | None:
            if replacement_finished and name == "000000.json":
                return pending_bytes
            return real_read_record(directory, name, max_record_bytes=max_record_bytes)

        monkeypatch.setattr(run_store_module, "replace_record", observe_replace)
        monkeypatch.setattr(run_store_module, "read_record", stale_terminal_readback)
        with pytest.raises(RunStoreError) as raised:
            store.complete_posting(
                index=0,
                expected_revision=dispatched.revision,
                response=expected,
            )

        assert raised.value.reason == "corrupt_record"

    monkeypatch.setattr(run_store_module, "replace_record", real_replace)
    monkeypatch.setattr(run_store_module, "read_record", real_read_record)
    with RunStore.open(**arguments) as reopened:
        restored = reopened.step(0)
        assert restored is not None
        assert restored.state == "completed"
        assert restored.intent == intent
        assert restored.result == {"status_code": 201, "body": expected.body}
        assert reopened.actors["poster"].private_key_path.read_bytes() == private_key


def test_reopen_rejects_malformed_completed_posting_without_allocating_new_identity(
    tmp_path: Path,
) -> None:
    arguments = _arguments(tmp_path / "state")
    with RunStore.open(**arguments) as store:
        private_key = _bind(store)
        dispatched, intent = _reserve_posting(store)
        response = _published_response(intent)
        completed = store.complete_posting(
            index=0,
            expected_revision=dispatched.revision,
            response=response,
        )
        assert completed.state == "completed"
        record = store.run_directory / "steps" / "000000.json"

    malformed = json.loads(record.read_bytes())
    malformed["result"]["body"]["request_sha256"] = "0" * 64
    malformed_bytes = json.dumps(
        malformed,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    record.write_bytes(malformed_bytes)
    record.chmod(0o600)

    with pytest.raises(RunStoreError) as raised:
        RunStore.open(**arguments)

    assert raised.value.reason == "corrupt_record"
    assert record.read_bytes() == malformed_bytes
    assert malformed["intent"]["operation_id"] == intent.operation_id
    assert malformed["intent"]["task_id"] == intent.task_id
    assert (record.parents[1] / "keys" / "poster" / "poster.key").read_bytes() == private_key
