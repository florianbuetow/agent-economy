"""One stopped backup preserves and resumes several lifecycle states."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from scripts.isolated_test_runtime import SERVICE_ORDER, IsolatedRuntime
from scripts.recovery_targets import build_target_bindings
from service_auth import AgentFactory
from service_auth.financial_identity import operation_id_for
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_commons.recovery_contract import BindingKind, RecoveryInventoryV1

from db_gateway_service.money_audit import audit_money
from db_gateway_service.recovery import (
    PublicationState,
    create_backup,
    restore_backup,
    verify_backup,
    verify_restore,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _version(task: dict[str, Any], status: str) -> int:
    assert task["status"] == status
    value = task["lifecycle_version"]
    assert type(value) is int
    return value


async def _post(poster: Any, title: str) -> dict[str, Any]:
    posting = poster.prepare_task_posting(
        title=title,
        spec=f"Return {title}.txt containing the requested result.",
        reward=10,
        bidding_deadline_seconds=3600,
        execution_deadline_seconds=3600,
        review_deadline_seconds=3600,
        acceptance_window_seconds=3600,
    )
    response = await poster.submit_task_posting(posting)
    assert response.status_code == 201
    result = response.body["result"]
    assert isinstance(result, dict)
    return result


async def _accept(poster: Any, worker: Any, task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task["task_id"])
    bid = await worker.submit_bid(
        task_id=task_id,
        amount=6,
        operation_id=operation_id_for("multistate-bid", (task_id, worker.agent_id)),
        expected_lifecycle_version=_version(task, "open"),
    )
    assert bid.status_code == 201
    accepted = await poster.accept_bid(
        task_id=task_id,
        bid_id=str(bid.body["bid_id"]),
        operation_id=operation_id_for("multistate-accept", (task_id, worker.agent_id)),
        expected_lifecycle_version=_version(task, "open"),
    )
    assert accepted.status_code == 200
    return accepted.body


async def _submit(poster: Any, worker: Any, title: str) -> dict[str, Any]:
    accepted = await _accept(poster, worker, await _post(poster, title))
    task_id = str(accepted["task_id"])
    await worker.upload_asset(task_id, f"{title}.txt", title.encode())
    submitted = await worker.submit_deliverable(
        task_id,
        operation_id=operation_id_for("multistate-submit", (task_id, worker.agent_id)),
        expected_lifecycle_version=_version(accepted, "accepted"),
    )
    assert submitted.status_code == 200
    return submitted.body


async def _dispute(poster: Any, worker: Any, title: str) -> dict[str, Any]:
    submitted = await _submit(poster, worker, title)
    task_id = str(submitted["task_id"])
    disputed = await poster.dispute_task(
        task_id,
        reason=f"{title} requires adjudication",
        operation_id=operation_id_for("multistate-dispute", (task_id, poster.agent_id)),
        expected_lifecycle_version=_version(submitted, "submitted"),
    )
    assert disputed.status_code == 200
    result = disputed.body
    assert result["status"] == "disputed"
    return result


async def _build_states(runtime: IsolatedRuntime) -> dict[str, str]:
    factory = AgentFactory(config_path=runtime.agent_config_path)
    poster = factory.user_agent("operator")
    worker = factory.create_agent("alice")
    try:
        await poster.register()
        await worker.register()
        try:
            await worker.create_account()
        except httpx.HTTPStatusError as error:
            assert error.response.status_code == 409
        poster_balance_before = int((await poster.get_balance())["balance"])
        worker_balance_before = int((await worker.get_balance())["balance"])

        open_task = await _post(poster, "open")
        accepted_task = await _accept(poster, worker, await _post(poster, "accepted"))
        submitted_task = await _submit(poster, worker, "submitted")
        disputed_task = await _dispute(poster, worker, "disputed")
        judging_task = await _dispute(poster, worker, "judging")
        await worker.submit_worker_rebuttal(
            str(judging_task["task_id"]),
            str(judging_task["dispute_id"]),
            "Resume this rebutted dispute after restore.",
        )
        pending_task = await _submit(poster, worker, "pending")
        pending_operation = operation_id_for(
            "multistate-pending-approval", (str(pending_task["task_id"]),)
        )
        runtime.stop_service("central-bank")
        with pytest.raises(httpx.HTTPStatusError):
            await poster.approve_task(
                str(pending_task["task_id"]),
                operation_id=pending_operation,
                expected_lifecycle_version=_version(pending_task, "submitted"),
            )
        current_pending = await poster.get_task(str(pending_task["task_id"]))
        assert current_pending["pending_transition"]["command"] == "approve_task"
        return {
            "open": str(open_task["task_id"]),
            "accepted": str(accepted_task["task_id"]),
            "submitted": str(submitted_task["task_id"]),
            "disputed": str(disputed_task["task_id"]),
            "judging": str(judging_task["task_id"]),
            "judging_dispute": str(judging_task["dispute_id"]),
            "pending": str(pending_task["task_id"]),
            "pending_operation": pending_operation,
            "pending_version": str(_version(pending_task, "submitted")),
            "poster_balance_before": str(poster_balance_before),
            "worker_balance_before": str(worker_balance_before),
        }
    finally:
        await worker.close()
        await poster.close()


def _seed_expiring_judging_lease(runtime: IsolatedRuntime, dispute_id: str) -> None:
    config_path = runtime.config_paths["court"]
    config = yaml.safe_load(config_path.read_text())
    signer = GatewayRequestSigner(
        GatewaySignerConfig(**config["db_gateway"]["signing"]),
        config_dir=config_path.parent,
    )
    request = httpx.Request(
        "POST",
        f"{runtime.service_url('db-gateway')}/court/claims/{dispute_id}/judging-lease",
        json={"lease_id": f"lease-{uuid.uuid4()}", "lease_seconds": 1},
    )
    signed = signer.sign_request(request, action="gateway.court.write")
    with httpx.Client(timeout=5.0) as client:
        response = client.send(signed)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "judging"


def _snapshot(database: Path, ids: dict[str, str]) -> dict[str, object]:
    task_ids = [
        ids[key]
        for key in ("open", "accepted", "submitted", "disputed", "judging", "pending")
    ]
    placeholders = ",".join("?" for _ in task_ids)
    with sqlite3.connect(database) as connection:
        tasks = dict(
            connection.execute(
                f"SELECT task_id,status FROM board_tasks WHERE task_id IN ({placeholders})",
                task_ids,
            ).fetchall()
        )
        judging = connection.execute(
            "SELECT status FROM court_claims WHERE claim_id=?", (ids["judging_dispute"],)
        ).fetchone()
        pending = connection.execute(
            "SELECT operation_state,effect_state FROM board_task_operations "
            "WHERE task_id=? AND command='approve_task' ORDER BY claimed_at_us DESC LIMIT 1",
            (ids["pending"],),
        ).fetchone()
    return {"tasks": tasks, "judging": judging, "pending": pending}


def _backup_restore_fresh(
    source: IsolatedRuntime, tmp_path: Path
) -> tuple[IsolatedRuntime, Path]:
    prepared = source.prepare_recovery_and_stop(
        registry_timeout_seconds=10.0,
        registry_max_attempts=2,
    )
    inventory = RecoveryInventoryV1.model_validate_json(prepared.inventory_path.read_bytes())
    assert all(
        not process.running and process.group_drained for process in inventory.process_ownership
    )
    work_root = tmp_path / "recovery-work"
    work_root.mkdir(mode=0o700)
    backup_root = source.run_dir / "backups"
    backup = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="multistate",
        work_root=work_root,
    )
    assert backup.state is PublicationState.COMPLETE
    assert verify_backup(
        backup_root=backup_root,
        backup_id="multistate",
        work_root=work_root,
    ).identity_complete

    fresh = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path / "fresh-runtime",
    )
    generated = fresh.run_dir.with_name(f"generated-{fresh.run_dir.name}")
    fresh.run_dir.rename(generated)
    replacements = {
        source.service_url(service): fresh.service_url(service) for service in SERVICE_ORDER
    }
    endpoint_values: dict[str, object] = {}
    for binding in inventory.field_bindings:
        if binding.binding_kind is BindingKind.FILESYSTEM:
            continue
        if binding.binding_kind is BindingKind.PORT:
            value: object = fresh.ports[binding.config_id]
        elif binding.binding_kind is BindingKind.ORIGINS:
            value = (
                fresh.service_url("ui"),
                f"http://localhost:{fresh.ports['ui']}",
            )
        else:
            value = replacements.get(str(binding.captured_value), binding.captured_value)
        endpoint_values[binding.binding_id] = value
    bindings = build_target_bindings(
        inventory,
        target_root=fresh.run_dir,
        process_cwds={config.logical_id: config.process_cwd for config in inventory.configs},
        agents_package_root=REPO_ROOT / "agents",
        endpoint_values=endpoint_values,
    )
    restored = restore_backup(
        backup_root=backup_root,
        backup_id="multistate",
        target_bindings=bindings,
        target_root=fresh.run_dir,
        work_root=work_root,
    )
    assert restored.state is PublicationState.COMPLETE
    verified = verify_restore(target_root=fresh.run_dir, work_root=work_root)
    assert verified.artifact_verified
    assert verified.snapshot_preserved
    assert verified.identity_complete
    return fresh, bindings.resource_paths["database.primary"]


async def _wait_for_recovery(  # noqa: PLR0915
    runtime: IsolatedRuntime, ids: dict[str, str]
) -> None:
    factory = AgentFactory(config_path=runtime.agent_config_path)
    poster = factory.user_agent("operator")
    worker = factory.create_agent("alice")
    platform = factory.platform_agent()
    try:
        await poster.register()
        await worker.register()
        await platform.register()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            judging = await poster.get_task(ids["judging"])
            if judging["status"] == "ruled":
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError(judging)

        pending = await poster.get_task(ids["pending"])
        assert pending["status"] in {"submitted", "approved"}
        if pending["status"] == "submitted":
            assert pending["pending_transition"]["command"] == "approve_task"
        for _ in range(2):
            completed_pending = await poster.approve_task(
                ids["pending"],
                operation_id=ids["pending_operation"],
                expected_lifecycle_version=int(ids["pending_version"]),
            )
            assert completed_pending.status_code == 200

        for state in ("submitted", "disputed", "judging", "pending"):
            assets = await poster.list_assets(ids[state])
            assert len(assets) == 1
            restored_bytes = await poster.download_asset(
                ids[state], str(assets[0]["asset_id"])
            )
            assert restored_bytes == state.encode()

        open_task = await poster.get_task(ids["open"])
        cancel_operation = operation_id_for("multistate-cancel", (ids["open"],))
        for _ in range(2):
            cancelled = await poster.cancel_task(
                ids["open"],
                operation_id=cancel_operation,
                expected_lifecycle_version=_version(open_task, "open"),
            )
            assert cancelled.status_code == 200

        accepted_task = await poster.get_task(ids["accepted"])
        accepted_version = _version(accepted_task, "accepted")
        accepted_asset = await worker.upload_asset(
            ids["accepted"], "accepted.txt", b"accepted"
        )
        assert await poster.download_asset(
            ids["accepted"], str(accepted_asset["asset_id"])
        ) == b"accepted"
        submit_operation = operation_id_for("multistate-submit-restored", (ids["accepted"],))
        for _ in range(2):
            submitted = await worker.submit_deliverable(
                ids["accepted"],
                operation_id=submit_operation,
                expected_lifecycle_version=accepted_version,
            )
            assert submitted.status_code == 200
        approve_accepted = operation_id_for(
            "multistate-approve-restored", (ids["accepted"],)
        )
        for _ in range(2):
            approved = await poster.approve_task(
                ids["accepted"],
                operation_id=approve_accepted,
                expected_lifecycle_version=_version(submitted.body, "submitted"),
            )
            assert approved.status_code == 200

        submitted_task = await poster.get_task(ids["submitted"])
        approve_submitted = operation_id_for(
            "multistate-approve-original", (ids["submitted"],)
        )
        for _ in range(2):
            approved = await poster.approve_task(
                ids["submitted"],
                operation_id=approve_submitted,
                expected_lifecycle_version=_version(submitted_task, "submitted"),
            )
            assert approved.status_code == 200

        disputed_task = await poster.get_task(ids["disputed"])
        dispute_id = str(disputed_task["dispute_id"])
        await worker.submit_worker_rebuttal(
            ids["disputed"], dispute_id, "Resume the restored disputed task."
        )
        ruling = await platform.trigger_ruling(dispute_id)
        assert ruling["status"] == "ruled"

        expected_statuses = {
            "open": "cancelled",
            "accepted": "approved",
            "submitted": "approved",
            "disputed": "ruled",
            "judging": "ruled",
            "pending": "approved",
        }
        for key, expected in expected_statuses.items():
            assert (await poster.get_task(ids[key]))["status"] == expected

        assert int((await poster.get_balance())["balance"]) == int(
            ids["poster_balance_before"]
        ) - 24
        assert int((await worker.get_balance())["balance"]) == int(
            ids["worker_balance_before"]
        ) + 24
        with sqlite3.connect(runtime.run_dir / "data" / "economy.db") as connection:
            assert audit_money(connection).financially_clean
    finally:
        await platform.close()
        await worker.close()
        await poster.close()


def test_multistate_backup_restores_and_resumes_judging_and_pending_settlement(
    tmp_path: Path,
) -> None:
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=tmp_path / "runtime")
    restored_runtime: IsolatedRuntime | None = None
    task_board_config = yaml.safe_load(runtime.config_paths["task-board"].read_text())
    task_board_config["deadline_evaluation"]["evaluation_interval_seconds"] = 3600
    task_board_config["financial_effect_recovery"]["interval_seconds"] = 3600
    task_board_config["central_bank"]["timeout_seconds"] = 1
    runtime.config_paths["task-board"].write_text(
        yaml.safe_dump(task_board_config, sort_keys=False)
    )
    court_config = yaml.safe_load(runtime.config_paths["court"].read_text())
    court_config["disputes"]["reclaim_interval_seconds"] = 3600
    runtime.config_paths["court"].write_text(yaml.safe_dump(court_config, sort_keys=False))
    try:
        runtime.start()
        assert runtime.provision() == 0
        ids = asyncio.run(_build_states(runtime))
        runtime.start_service("central-bank")
        _seed_expiring_judging_lease(runtime, ids["judging_dispute"])
        database = runtime.run_dir / "data" / "economy.db"
        before = _snapshot(database, ids)
        assert before["tasks"] == {
            ids["open"]: "open",
            ids["accepted"]: "accepted",
            ids["submitted"]: "submitted",
            ids["disputed"]: "disputed",
            ids["judging"]: "disputed",
            ids["pending"]: "submitted",
        }
        assert before["judging"] == ("judging",)
        assert before["pending"] == ("claimed", "pending")

        restored_runtime, restored_database = _backup_restore_fresh(runtime, tmp_path)
        assert _snapshot(restored_database, ids) == before
        restored_runtime.start()
        asyncio.run(_wait_for_recovery(restored_runtime, ids))
    finally:
        runtime.stop()
        if restored_runtime is not None:
            restored_runtime.stop()


async def _prepare_during_streaming_upload(  # noqa: PLR0915
    runtime: IsolatedRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = AgentFactory(config_path=runtime.agent_config_path)
    poster = factory.user_agent("operator")
    worker = factory.create_agent("alice")
    started = asyncio.Event()
    release = asyncio.Event()
    stop_started = threading.Event()
    client: httpx.AsyncClient | None = None
    upload: asyncio.Task[httpx.Response] | None = None
    preparation: asyncio.Task[Any] | None = None
    primary: BaseException | None = None
    cleanup_failures: list[BaseException] = []
    try:
        await poster.register()
        await worker.register()
        try:
            await worker.create_account()
        except httpx.HTTPStatusError as error:
            assert error.response.status_code == 409
        accepted = await _accept(poster, worker, await _post(poster, "active-upload"))
        task_id = str(accepted["task_id"])
        boundary = "adversarial-boundary"
        private_bytes = b"active-upload-bytes" * 1024
        prefix = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="active.bin"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        suffix = b"\r\n" + f"--{boundary}--\r\n".encode()

        async def stream():
            yield prefix + private_bytes[:1024]
            started.set()
            await release.wait()
            yield private_bytes[1024:] + suffix

        headers = worker._auth_header(
            {"action": "upload_asset", "task_id": task_id},
            audience="task-board",
            resource=f"POST /tasks/{task_id}/assets",
        )
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        headers["Idempotency-Key"] = "active-upload-backup"
        client = httpx.AsyncClient(timeout=20.0)
        upload = asyncio.create_task(
            client.post(
                f"{runtime.service_url('task-board')}/tasks/{task_id}/assets",
                headers=headers,
                content=stream(),
            )
        )
        await asyncio.wait_for(started.wait(), timeout=5)

        original_stop = runtime.stop

        def signaled_stop() -> None:
            stop_started.set()
            original_stop()

        monkeypatch.setattr(runtime, "stop", signaled_stop)
        preparation = asyncio.create_task(
            asyncio.to_thread(
                runtime.prepare_recovery_and_stop,
                registry_timeout_seconds=10.0,
                registry_max_attempts=2,
            )
        )
        await asyncio.wait_for(asyncio.to_thread(stop_started.wait), timeout=10)
        release.set()
        prepared = await preparation
        result = await asyncio.gather(upload, return_exceptions=True)
        await client.aclose()

        database = runtime.run_dir / "data" / "economy.db"
        with sqlite3.connect(database) as connection:
            ready = connection.execute(
                "SELECT COUNT(*) FROM board_artifact_uploads "
                "WHERE task_id=? AND state='ready'",
                (task_id,),
            ).fetchone()[0]
            visible = connection.execute(
                "SELECT COUNT(*) FROM board_assets WHERE task_id=?", (task_id,)
            ).fetchone()[0]
        response = result[0]
        if isinstance(response, httpx.Response) and response.status_code == 201:
            assert ready == visible == 1
        else:
            assert ready == visible == 0

        inventory = RecoveryInventoryV1.model_validate_json(prepared.inventory_path.read_bytes())
        work_root = tmp_path / "active-write-recovery-work"
        work_root.mkdir(mode=0o700)
        backup = create_backup(
            inventory,
            backup_root=runtime.run_dir / "backups",
            backup_id="active-write",
            work_root=work_root,
        )
        assert backup.state is PublicationState.COMPLETE
        assert verify_backup(
            backup_root=runtime.run_dir / "backups",
            backup_id="active-write",
            work_root=work_root,
        ).snapshot_preserved
    except BaseException as error:
        primary = error
    finally:
        release.set()
        if upload is not None:
            if not upload.done():
                upload.cancel()
            upload_result = await asyncio.gather(upload, return_exceptions=True)
            cleanup_failures.extend(
                result
                for result in upload_result
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            )
        if preparation is not None:
            try:
                await asyncio.wait_for(asyncio.shield(preparation), timeout=30)
            except BaseException as error:
                cleanup_failures.append(error)
        if client is not None:
            try:
                await client.aclose()
            except BaseException as error:
                cleanup_failures.append(error)
        close_results = await asyncio.gather(
            worker.close(), poster.close(), return_exceptions=True
        )
        cleanup_failures.extend(
            result for result in close_results if isinstance(result, BaseException)
        )
    failures = ([primary] if primary is not None else []) + cleanup_failures
    if failures:
        raise BaseExceptionGroup("active upload recovery test failed", failures)


def test_backup_anchors_or_safely_aborts_a_concurrent_authenticated_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = IsolatedRuntime.create(
        repo_root=REPO_ROOT,
        artifacts_root=tmp_path / "active-runtime",
    )
    try:
        runtime.start()
        assert runtime.provision() == 0
        asyncio.run(_prepare_during_streaming_upload(runtime, tmp_path, monkeypatch))
    finally:
        runtime.stop()
