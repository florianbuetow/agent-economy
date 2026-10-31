"""T-126 real-Gateway quota and lifecycle atomicity acceptance proofs."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_commons.exceptions import ServiceError

from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response

    from task_board_service.services.protocol import TaskStorageInterface

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_THREAD_TIMEOUT_SECONDS = 10
_MAX_FILE_SIZE_BYTES = 32


class _LiveGateway(Protocol):
    base_url: str


class _TaskSetup(Protocol):
    task_id: str
    poster_id: str
    worker_id: str


class _BidSetup(Protocol):
    bid_id: str


_replay_helpers = importlib.import_module("tests.integration.test_mp07_public_command_replay")
_create_open_task = cast(
    "Callable[[AsyncClient, AsyncClient, Ed25519PrivateKey], Awaitable[_TaskSetup]]",
    _replay_helpers.__dict__["_create_open_task"],
)
_create_bid = cast(
    "Callable[..., Awaitable[_BidSetup]]",
    _replay_helpers.__dict__["_create_bid"],
)
_accept_payload = cast(
    "Callable[..., dict[str, Any]]",
    _replay_helpers.__dict__["_accept_payload"],
)
_post_signed = cast(
    "Callable[..., Awaitable[Response]]",
    _replay_helpers.__dict__["_post_signed"],
)


@dataclass(frozen=True)
class _AcceptedTask:
    task_id: str
    poster_id: str
    worker_id: str


@dataclass(frozen=True)
class _UploadOutcome:
    result: dict[str, Any] | None
    error: ServiceError | None


class _AcceptedTokenValidator:
    def __init__(self, *, task_id: str, worker_id: str) -> None:
        self._task_id = task_id
        self._worker_id = worker_id

    async def validate_jws_token(
        self,
        token: str,
        expected_action: str | tuple[str, ...],
        *,
        expected_resource: str,
    ) -> dict[str, Any]:
        assert token == "test-token"
        assert expected_action == "upload_asset"
        assert expected_resource == f"POST /tasks/{self._task_id}/assets"
        return {
            "action": "upload_asset",
            "task_id": self._task_id,
            "_signer_id": self._worker_id,
        }


class _IdentityDeadlineEvaluator:
    async def evaluate_deadline(self, task: dict[str, Any]) -> dict[str, Any]:
        return task


class _PausingDeadlineEvaluator:
    def __init__(self, *, accepted_read: threading.Event, resume: threading.Event) -> None:
        self._accepted_read = accepted_read
        self._resume = resume

    async def evaluate_deadline(self, task: dict[str, Any]) -> dict[str, Any]:
        assert task["status"] == "accepted"
        self._accepted_read.set()
        assert self._resume.wait(timeout=_THREAD_TIMEOUT_SECONDS), (
            "test did not resume the upload after the lifecycle transition"
        )
        return task


class _CountBarrierStore:
    def __init__(self, store: TaskDbClient, barrier: threading.Barrier) -> None:
        self._store = store
        self._barrier = barrier
        self.observed_counts: list[int] = []

    def count_assets(self, task_id: str) -> int:
        count = self._store.count_assets(task_id)
        self.observed_counts.append(count)
        self._barrier.wait(timeout=_THREAD_TIMEOUT_SECONDS)
        return count

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


def _gateway_store(gateway: _LiveGateway, gateway_db_path: str) -> TaskDbClient:
    key_dir = Path(gateway_db_path).parent
    signer = GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-task-board",
            private_key_path=key_dir / "gateway-task-board.key",
            audience="db-gateway",
            token_ttl_seconds=300,
        ),
        config_dir=key_dir,
    )
    return TaskDbClient(base_url=gateway.base_url, timeout_seconds=10, signer=signer)


def _manager(
    *,
    store: object,
    task: _AcceptedTask,
    storage_root: Path,
    max_files_per_task: int,
    deadline_evaluator: object,
) -> AssetManager:
    return AssetManager(
        store=cast("TaskStorageInterface", store),
        token_validator=cast(
            "Any",
            _AcceptedTokenValidator(task_id=task.task_id, worker_id=task.worker_id),
        ),
        deadline_evaluator=cast("Any", deadline_evaluator),
        asset_storage_path=str(storage_root),
        max_file_size=_MAX_FILE_SIZE_BYTES,
        max_files_per_task=max_files_per_task,
    )


async def _accepted_task(
    *,
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_key: Ed25519PrivateKey,
    worker_key: Ed25519PrivateKey,
) -> _AcceptedTask:
    setup = await _create_open_task(client, gateway_client, poster_key)
    bid = await _create_bid(
        client,
        worker_key,
        setup,
        operation_id=f"op-{uuid.uuid4()}",
        amount=90,
    )
    response = await _post_signed(
        client,
        path=f"/tasks/{setup.task_id}/bids/{bid.bid_id}/accept",
        private_key=poster_key,
        signer_id=setup.poster_id,
        payload=_accept_payload(
            bid,
            operation_id=f"op-{uuid.uuid4()}",
            expected_lifecycle_version=0,
        ),
    )
    assert response.status_code == 200, response.text
    accepted = response.json()
    assert accepted["status"] == "accepted"
    assert accepted["lifecycle_version"] == 1
    return _AcceptedTask(setup.task_id, setup.poster_id, setup.worker_id)


def _run_upload(
    manager: AssetManager,
    task_id: str,
    *,
    filename: str,
    content: bytes,
) -> _UploadOutcome:
    try:
        result = asyncio.run(
            manager.upload_asset(task_id, "test-token", content, filename, "text/plain")
        )
        return _UploadOutcome(result=result, error=None)
    except ServiceError as exc:
        return _UploadOutcome(result=None, error=exc)


def _asset_rows(db_path: str, task_id: str) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT asset_id, filename, size_bytes, content_hash FROM board_assets "
                "WHERE task_id = ? ORDER BY asset_id",
                (task_id,),
            ).fetchall()
        ]
    finally:
        connection.close()


def _complete_asset_rows(db_path: str, task_id: str) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT asset_id, task_id, uploader_id, filename, content_type, "
                "size_bytes, storage_path, content_hash, uploaded_at FROM board_assets "
                "WHERE task_id = ? ORDER BY asset_id",
                (task_id,),
            ).fetchall()
        ]
    finally:
        connection.close()


def _stored_files(storage_root: Path) -> list[Path]:
    return sorted(path for path in storage_root.rglob("*") if path.is_file())


def _file_snapshot(storage_root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(storage_root)): path.read_bytes()
        for path in _stored_files(storage_root)
    }


def _task_state(db_path: str, task_id: str) -> dict[str, object]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT t.status, c.lifecycle_version FROM board_tasks AS t "
            "JOIN board_task_contracts AS c ON c.task_id = t.task_id "
            "WHERE t.task_id = ?",
            (task_id,),
        ).fetchone()
        assert row is not None
        return dict(row)
    finally:
        connection.close()


@pytest.mark.integration
async def test_two_board_instances_cannot_both_publish_at_one_file_limit(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    _gateway: _LiveGateway,
    tmp_path: Path,
) -> None:
    """A real split count/write race cannot publish two artifacts at limit one."""
    task = await _accepted_task(
        client=client,
        gateway_client=gateway_client,
        poster_key=poster_keypair[0],
        worker_key=worker_keypair[0],
    )
    storage_root = tmp_path / "atomic-quota-assets"
    barrier = threading.Barrier(2)
    clients = [
        _gateway_store(_gateway, gateway_db_path),
        _gateway_store(_gateway, gateway_db_path),
    ]
    stores = [_CountBarrierStore(store, barrier) for store in clients]
    managers = [
        _manager(
            store=store,
            task=task,
            storage_root=storage_root,
            max_files_per_task=1,
            deadline_evaluator=_IdentityDeadlineEvaluator(),
        )
        for store in stores
    ]
    attempts = [
        {"filename": f"answer-{index}.txt", "content": f"answer-{index}".encode()}
        for index in range(2)
    ]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _run_upload,
                    manager,
                    task.task_id,
                    filename=cast("str", attempt["filename"]),
                    content=cast("bytes", attempt["content"]),
                )
                for manager, attempt in zip(managers, attempts, strict=True)
            ]
            outcomes = [future.result(timeout=_THREAD_TIMEOUT_SECONDS) for future in futures]

        observed_counts = [count for store in stores for count in store.observed_counts]
        assert observed_counts == [0, 0], (
            "the fixture did not reach the intended split-decision race: "
            f"observed_counts={observed_counts}"
        )
        rows = _asset_rows(gateway_db_path, task.task_id)
        files = _stored_files(storage_root)
        success_indices = [
            index for index, outcome in enumerate(outcomes) if outcome.result is not None
        ]
        error_indices = [
            index for index, outcome in enumerate(outcomes) if outcome.error is not None
        ]
        errors = [cast("ServiceError", outcomes[index].error).error for index in error_indices]
        diagnostics = {
            "success_indices": success_indices,
            "error_indices": error_indices,
            "error_codes": errors,
            "metadata_rows": rows,
            "stored_files": [str(path.relative_to(storage_root)) for path in files],
            "stored_bytes": [path.read_bytes() for path in files],
        }

        assert (success_indices, error_indices, errors, len(rows), len(files)) in (
            ([0], [1], ["too_many_assets"], 1, 1),
            ([1], [0], ["too_many_assets"], 1, 1),
        ), diagnostics

        winner_index = success_indices[0]
        loser_index = error_indices[0]
        winner = attempts[winner_index]
        loser = attempts[loser_index]
        result = cast("dict[str, Any]", outcomes[winner_index].result)
        winner_filename = cast("str", winner["filename"])
        winner_content = cast("bytes", winner["content"])
        loser_filename = cast("str", loser["filename"])
        loser_content = cast("bytes", loser["content"])
        winner_hash = hashlib.sha256(winner_content).hexdigest()
        assert result["filename"] == winner_filename, diagnostics
        assert result["size_bytes"] == len(winner_content), diagnostics
        assert result["content_hash"] == winner_hash, diagnostics
        assert rows == [
            {
                "asset_id": result["asset_id"],
                "filename": winner_filename,
                "size_bytes": len(winner_content),
                "content_hash": winner_hash,
            }
        ], diagnostics
        assert files[0].read_bytes() == winner_content, diagnostics
        assert loser_filename not in {cast("str", row["filename"]) for row in rows}, diagnostics
        assert loser_content not in [path.read_bytes() for path in files], diagnostics
        assert await managers[winner_index].download_asset(
            task.task_id, cast("str", result["asset_id"])
        ) == (winner_content, "text/plain", winner_filename), diagnostics
    finally:
        for gateway_store in clients:
            gateway_store.close()


@pytest.mark.integration
async def test_upload_that_read_accepted_cannot_publish_after_submission_wins(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    _gateway: _LiveGateway,
    tmp_path: Path,
) -> None:
    """A submitted lifecycle state fences an upload that read accepted earlier."""
    task = await _accepted_task(
        client=client,
        gateway_client=gateway_client,
        poster_key=poster_keypair[0],
        worker_key=worker_keypair[0],
    )
    storage_root = tmp_path / "lifecycle-race-assets"
    baseline_store = _gateway_store(_gateway, gateway_db_path)
    upload_store = _gateway_store(_gateway, gateway_db_path)
    transition_store = _gateway_store(_gateway, gateway_db_path)
    accepted_read = threading.Event()
    resume = threading.Event()
    try:
        baseline_manager = _manager(
            store=baseline_store,
            task=task,
            storage_root=storage_root,
            max_files_per_task=2,
            deadline_evaluator=_IdentityDeadlineEvaluator(),
        )
        baseline = await baseline_manager.upload_asset(
            task.task_id,
            "test-token",
            b"ready",
            "ready.txt",
            "text/plain",
        )
        baseline_rows = _complete_asset_rows(gateway_db_path, task.task_id)
        baseline_files = _file_snapshot(storage_root)
        baseline_uuid = uuid.UUID(cast("str", baseline["asset_id"]).removeprefix("asset-"))
        assert baseline_rows == [
            {
                "asset_id": baseline["asset_id"],
                "task_id": task.task_id,
                "uploader_id": task.worker_id,
                "filename": "ready.txt",
                "content_type": "text/plain",
                "size_bytes": 5,
                "storage_path": (
                    f"live/{hashlib.sha256(task.worker_id.encode()).hexdigest()[:32]}/"
                    f"{baseline_uuid.hex[:2]}/{baseline_uuid}.blob"
                ),
                "content_hash": hashlib.sha256(b"ready").hexdigest(),
                "uploaded_at": baseline["uploaded_at"],
            }
        ]
        assert list(baseline_files.values()) == [b"ready"]
        pausing_manager = _manager(
            store=upload_store,
            task=task,
            storage_root=storage_root,
            max_files_per_task=2,
            deadline_evaluator=_PausingDeadlineEvaluator(
                accepted_read=accepted_read,
                resume=resume,
            ),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _run_upload,
                pausing_manager,
                task.task_id,
                filename="late.txt",
                content=b"late",
            )
            assert await asyncio.to_thread(accepted_read.wait, _THREAD_TIMEOUT_SECONDS), (
                "upload never reached the accepted-state observation fence"
            )
            submission = transition_store.claim_task_operation(
                {
                    "protocol_version": 1,
                    "operation_id": f"op-{uuid.uuid4()}",
                    "task_id": task.task_id,
                    "command": "submit_deliverable",
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "actor_kind": "agent",
                    "actor_id": task.worker_id,
                    "arguments": {"worker_id": task.worker_id},
                }
            )
            assert submission["operation_state"] == "completed", submission
            assert submission["result"]["status"] == "submitted", submission
            resume.set()
            outcome = future.result(timeout=_THREAD_TIMEOUT_SECONDS)
    finally:
        resume.set()
        baseline_store.close()
        upload_store.close()
        transition_store.close()

    rows = _complete_asset_rows(gateway_db_path, task.task_id)
    files = _file_snapshot(storage_root)
    final_task = _task_state(gateway_db_path, task.task_id)
    error_code = None if outcome.error is None else outcome.error.error
    diagnostics = {
        "baseline_asset_id": baseline["asset_id"],
        "upload_result": outcome.result,
        "upload_error": error_code,
        "baseline_rows": baseline_rows,
        "metadata_rows": rows,
        "baseline_files": baseline_files,
        "stored_files": files,
        "final_task": final_task,
    }

    assert final_task == {"status": "submitted", "lifecycle_version": 2}, diagnostics
    assert outcome.result is None and error_code == "task_version_conflict", diagnostics
    assert rows == baseline_rows, diagnostics
    assert files == baseline_files, diagnostics
