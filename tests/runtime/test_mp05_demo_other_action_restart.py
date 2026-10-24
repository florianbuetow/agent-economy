"""MP-05 restart proof for non-native demo effects and direct Court actions."""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
import uuid
from builtins import BaseExceptionGroup
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest
import scripts.isolated_test_runtime as runtime_module
import yaml
from scripts.isolated_test_runtime import (
    IsolatedRuntime,
    _drain_owned_process_group,
    _release_owned_launch,
    _start_owned_launch,
)
from service_auth.signing import create_jws, load_keypair, public_key_to_b64

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = REPO_ROOT / "tools"
CRASH_RUNNER = Path(__file__).with_name("mp05_demo_other_action_crash_runner.py")
JUDGING_SERVER = Path(__file__).with_name("mp05_demo_judging_phase_server.py")
TIMEOUT_SECONDS = 75.0
FUNDING_AMOUNT = 500
REWARD = 100
BID_AMOUNT = 80
EFFECT_TABLE = {
    "upload_asset": "board_assets",
    "feedback": "reputation_feedback",
    "file_claim": "court_claims",
    "submit_rebuttal": "court_rebuttals",
    "trigger_ruling": "court_rulings",
}
NONREPLAYABLE = {"upload_asset", "feedback"}
PROJECTION_TABLES = (
    "identity_agents",
    "bank_accounts",
    "bank_transactions",
    "bank_escrow",
    "bank_operation_receipts",
    "bank_operation_legs",
    "board_tasks",
    "board_task_publications",
    "board_bids",
    "board_assets",
    "board_task_operations",
    "reputation_feedback",
    "court_claims",
    "court_rebuttals",
    "court_rulings",
    "events",
)
PRIMARY_KEYS = {
    "identity_agents": ("agent_id",),
    "bank_accounts": ("account_id",),
    "bank_transactions": ("tx_id",),
    "bank_escrow": ("escrow_id",),
    "bank_operation_receipts": ("operation_id",),
    "bank_operation_legs": ("operation_id", "leg_index"),
    "board_tasks": ("task_id",),
    "board_task_publications": ("operation_id",),
    "board_bids": ("bid_id",),
    "board_assets": ("asset_id",),
    "board_task_operations": ("operation_id",),
    "reputation_feedback": ("feedback_id",),
    "court_claims": ("claim_id",),
    "court_rebuttals": ("rebuttal_id",),
    "court_rulings": ("ruling_id",),
    "events": ("event_id",),
}


def _expected_added_counts(action: str, phase: str) -> dict[str, int]:
    counts = {
        "identity_agents": 2,
        "bank_accounts": 2,
        "bank_transactions": 2,
        "bank_escrow": 1,
        "bank_operation_receipts": 2,
        "bank_operation_legs": 2,
        "board_tasks": 1,
        "board_task_publications": 1,
        "board_bids": 1,
        "board_assets": 0 if action == "upload_asset" and phase == "before_send" else 1,
        "board_task_operations": {
            "upload_asset": 2,
            "file_claim": 3,
            "submit_rebuttal": 4,
            "trigger_ruling": 5 if phase == "after_response" else 4,
            "feedback": 4,
        }[action],
        "reputation_feedback": (
            2
            if action == "trigger_ruling" and phase == "after_response"
            else int(action == "feedback" and phase == "after_response")
        ),
        "court_claims": int(
            (action == "file_claim" and phase == "after_response")
            or action in {"submit_rebuttal", "trigger_ruling"}
        ),
        "court_rebuttals": int(
            (action == "submit_rebuttal" and phase == "after_response")
            or action == "trigger_ruling"
        ),
        "court_rulings": int(action == "trigger_ruling" and phase == "after_response"),
        "events": {
            ("upload_asset", "before_send"): 9,
            ("upload_asset", "after_response"): 10,
            ("file_claim", "before_send"): 11,
            ("file_claim", "after_response"): 12,
            ("submit_rebuttal", "before_send"): 13,
            ("submit_rebuttal", "after_response"): 14,
            ("trigger_ruling", "before_send"): 14,
            ("trigger_ruling", "after_response"): 20,
            ("feedback", "before_send"): 13,
            ("feedback", "after_response"): 14,
        }[(action, phase)],
    }
    if action == "feedback":
        counts.update(
            bank_transactions=3,
            bank_operation_receipts=3,
            bank_operation_legs=3,
        )
    elif action == "trigger_ruling" and phase == "after_response":
        counts.update(
            bank_transactions=4,
            bank_operation_receipts=3,
            bank_operation_legs=4,
        )
    return counts


def _expected_event_types(action: str, phase: str) -> Counter[str]:
    values = Counter(
        {
            "agent.registered": 2,
            "account.created": 2,
            "salary.paid": 1,
            "escrow.locked": 1,
            "task.created": 1,
            "bid.submitted": 1,
            "task.accepted": 1,
        }
    )
    if action != "upload_asset" or phase == "after_response":
        values["asset.uploaded"] += 1
    if action != "upload_asset":
        values["task.submitted"] += 1
    if action == "feedback":
        values.update({"escrow.released": 1, "task.approved": 1})
        if phase == "after_response":
            values["feedback.submitted"] += 1
    if action == "file_claim" and phase == "after_response":
        values["claim.filed"] += 1
    if action in {"submit_rebuttal", "trigger_ruling"}:
        values.update({"claim.filed": 1, "task.disputed": 1})
    if (action == "submit_rebuttal" and phase == "after_response") or action == "trigger_ruling":
        values["rebuttal.submitted"] += 1
    if action == "trigger_ruling" and phase == "after_response":
        values.update(
            {
                "claim.status_changed": 1,
                "escrow.split": 1,
                "task.ruled": 1,
                "feedback.submitted": 2,
                "ruling.delivered": 1,
            }
        )
    return values


@dataclass
class _OwnedChild:
    process: subprocess.Popen[bytes]
    pgid: int
    sid: int
    actor_pid: int | None = None
    drained: bool = False

    def bind_actor(self, actor_pid: int) -> None:
        if actor_pid <= 0:
            raise RuntimeError("Crash runner PID must be positive")
        if os.getpgid(actor_pid) != self.pgid or os.getsid(actor_pid) != self.sid:
            raise RuntimeError("Crash runner escaped its owned process group")
        self.actor_pid = actor_pid


@dataclass(slots=True)
class _LaunchGate:
    descriptor: int | None

    def release(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            raise RuntimeError("Launch gate is no longer owned")
        try:
            _release_owned_launch(descriptor)
        finally:
            self.descriptor = None

    def close(self) -> None:
        descriptor = self.descriptor
        if descriptor is None:
            return
        self.descriptor = None
        os.close(descriptor)


@dataclass(slots=True)
class _Harness:
    runtime: IsolatedRuntime
    config_path: Path
    state_directory: Path
    artifacts: Path
    sentinel: _OwnedChild


def _group_exists(child: _OwnedChild) -> bool:
    try:
        os.killpg(child.pgid, 0)
    except ProcessLookupError:
        return False
    return True


def _terminate_owned(child: _OwnedChild) -> None:
    if child.drained:
        return
    _drain_owned_process_group(
        child.process,
        pgid=child.pgid,
        sid=child.sid,
    )
    child.drained = True


def _capture_cleanup(failures: list[BaseException], callback: Any, *arguments: object) -> None:
    try:
        callback(*arguments)
    except BaseException as exc:
        failures.append(exc)


def _raise_failures(
    primary: BaseException | None,
    cleanup_failures: list[BaseException],
    evidence_path: Path,
) -> None:
    failures = ([] if primary is None else [primary]) + cleanup_failures
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise BaseExceptionGroup(
            f"MP05 other-action restart proof failed; evidence: {evidence_path}", failures
        )


def _launch(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    with log_path.open("wb") as log:
        process, pgid, sid, descriptor = _start_owned_launch(
            command,
            cwd=str(cwd),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            pass_fds=(),
        )
        child = _OwnedChild(process=process, pgid=pgid, sid=sid)
        ownership.callback(_capture_cleanup, cleanup_failures, _terminate_owned, child)
        gate = _LaunchGate(descriptor)
        ownership.callback(_capture_cleanup, cleanup_failures, gate.close)
        gate.release()
    return child


def _start_sentinel(
    root: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    child = _launch(
        ["/bin/sleep", "600"],
        cwd=root,
        env=dict(os.environ),
        log_path=root / "other-action-sentinel.log",
        ownership=ownership,
        cleanup_failures=cleanup_failures,
    )
    child.bind_actor(child.process.pid)
    return child


@pytest.fixture
def other_action_harness(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[_Harness]:
    artifacts = tmp_path_factory.mktemp("mp05-demo-other-action", numbered=True)
    runtime = IsolatedRuntime.create(repo_root=REPO_ROOT, artifacts_root=artifacts)
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    ownership.callback(_capture_cleanup, cleanup_failures, runtime.stop)
    primary: BaseException | None = None
    try:
        sentinel = _start_sentinel(
            runtime.run_dir,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        runtime.start()
        court_config = yaml.safe_load(runtime.config_paths["court"].read_text(encoding="utf-8"))
        assert court_config["judges"]["judges"] == [
            {"id": "judge-0", "provider": "mock", "model": "mock-judge"}
        ]
        config = yaml.safe_load((TOOLS_ROOT / "config.yaml").read_text(encoding="utf-8"))
        config["platform"].update(
            {
                "identity_url": runtime.service_url("identity"),
                "bank_url": runtime.service_url("central-bank"),
                "task_board_url": runtime.service_url("task-board"),
                "reputation_url": runtime.service_url("reputation"),
                "court_url": runtime.service_url("court"),
                "platform_key_path": "../keys/platform.key",
            }
        )
        config["demo_replay"] = {
            "environment_id": runtime.run_id,
            "state_directory": "../demo-other-action/state",
            "max_record_bytes": 1_048_576,
            "diagnostic_limit": 16,
        }
        config_path = runtime.run_dir / "configs" / "demo-other-action-tools.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        yield _Harness(
            runtime=runtime,
            config_path=config_path,
            state_directory=runtime.run_dir / "demo-other-action" / "state",
            artifacts=artifacts,
            sentinel=sentinel,
        )
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
    _raise_failures(primary, cleanup_failures, artifacts / "fixture-cleanup.json")


def _base_steps(run_id: str) -> list[dict[str, object]]:
    return [
        {"action": "register", "agent": "poster"},
        {"action": "register", "agent": "worker"},
        {
            "action": "fund",
            "agent": "poster",
            "amount": FUNDING_AMOUNT,
            "reference": f"mp05:other:{run_id}:fund:poster",
        },
        {
            "action": "post_task",
            "poster": "poster",
            "ref": "restart_task",
            "title": f"MP05 other action {run_id}",
            "spec": "Exercise one durable recovery boundary with real services.",
            "reward": REWARD,
            "bidding_deadline_seconds": 3600,
            "acceptance_window_seconds": 300,
            "execution_deadline_seconds": 7200,
            "review_deadline_seconds": 3600,
        },
        {
            "action": "bid",
            "bidder": "worker",
            "task_ref": "restart_task",
            "amount": BID_AMOUNT,
        },
        {
            "action": "accept_bid",
            "poster": "poster",
            "bidder": "worker",
            "task_ref": "restart_task",
        },
    ]


def _steps(action: str, run_id: str) -> list[dict[str, object]]:
    steps = _base_steps(run_id)
    upload = {
        "action": "upload_asset",
        "worker": "worker",
        "task_ref": "restart_task",
        "filename": "other-action-proof.txt",
        "content": f"real content for {action}",
    }
    if action == "upload_asset":
        return [*steps, upload]
    steps.extend(
        (
            upload,
            {"action": "submit_deliverable", "worker": "worker", "task_ref": "restart_task"},
        )
    )
    if action == "feedback":
        steps.extend(
            (
                {"action": "approve", "poster": "poster", "task_ref": "restart_task"},
                {
                    "action": "feedback",
                    "agent": "poster",
                    "task_ref": "restart_task",
                    "to_agent_id": "worker",
                    "category": "delivery_quality",
                    "rating": "satisfied",
                    "comment": "Exact one-shot feedback restart proof.",
                },
            )
        )
        return steps
    if action == "file_claim":
        steps.append(
            {
                "action": "file_claim",
                "poster": "poster",
                "worker": "worker",
                "task_ref": "restart_task",
                "claim": "The submitted artifact does not meet the written specification.",
            }
        )
        return steps
    steps.append(
        {
            "action": "dispute",
            "poster": "poster",
            "task_ref": "restart_task",
            "reason": "The submitted artifact does not meet the written specification.",
        }
    )
    rebuttal = {
        "action": "submit_rebuttal",
        "worker": "worker",
        "task_ref": "restart_task",
        "rebuttal": "The artifact satisfies every explicit acceptance criterion.",
    }
    if action == "submit_rebuttal":
        return [*steps, rebuttal]
    return [
        *steps,
        rebuttal,
        {"action": "trigger_ruling", "agent": "poster", "task_ref": "restart_task"},
    ]


def _write_scenario(case: Path, action: str, run_id: str) -> tuple[Path, int]:
    steps = _steps(action, run_id)
    scenario = {
        "name": f"MP-05 other action restart: {action}",
        "description": "Short real-process recovery-boundary proof.",
        "default_delay": 0,
        "agents": [
            {"handle": "poster", "name": f"MP05 other poster {run_id}"},
            {"handle": "worker", "name": f"MP05 other worker {run_id}"},
        ],
        "steps": steps,
    }
    path = case / "scenario.yaml"
    path.write_text(yaml.safe_dump(scenario, sort_keys=False), encoding="utf-8")
    return path, len(steps) - 1


def _environment(harness: _Harness) -> dict[str, str]:
    return {**harness.runtime.environment(), "TOOLS_CONFIG_PATH": str(harness.config_path)}


def _production_arguments(scenario_path: Path, run_id: str) -> list[str]:
    return [str(scenario_path), "--run-id", run_id, "--delay", "0"]


def _wait_for_gate(child: _OwnedChild, gate_path: Path, log_path: Path) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if gate_path.exists():
            value = json.loads(gate_path.read_text(encoding="utf-8"))
            assert isinstance(value, dict)
            return value
        return_code = child.process.poll()
        if return_code is not None:
            log = log_path.read_text(encoding="utf-8")
            raise AssertionError(
                "Demo CLI exited before the target gate; this is a setup/validation "
                f"failure, not target recovery behavior (exit {return_code}).\n{log}"
            )
        time.sleep(0.05)
    raise TimeoutError(f"Demo CLI did not reach the target gate; setup/validation log: {log_path}")


def _kill_at_gate(child: _OwnedChild, gate: dict[str, Any]) -> None:
    actor_pid = gate["pid"]
    assert type(actor_pid) is int
    child.bind_actor(actor_pid)
    assert gate["pgid"] == child.pgid and gate["sid"] == child.sid
    os.kill(actor_pid, signal.SIGKILL)
    assert child.process.wait(timeout=TIMEOUT_SECONDS) != 0
    assert not _group_exists(child)
    child.drained = True


def _launch_crash(
    harness: _Harness,
    scenario_path: Path,
    run_id: str,
    action: str,
    phase: str,
    gate_path: Path,
    log_path: Path,
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
) -> _OwnedChild:
    return _launch(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            str(CRASH_RUNNER),
            "--action",
            action,
            "--phase",
            phase,
            "--gate",
            str(gate_path),
            "--",
            *_production_arguments(scenario_path, run_id),
        ],
        cwd=TOOLS_ROOT,
        env=_environment(harness),
        log_path=log_path,
        ownership=ownership,
        cleanup_failures=cleanup_failures,
    )


def _run_cli(harness: _Harness, scenario_path: Path, run_id: str, log_path: Path) -> int:
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    primary: BaseException | None = None
    return_code: int | None = None
    try:
        child = _launch(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-m",
                "demo_replay",
                *_production_arguments(scenario_path, run_id),
            ],
            cwd=TOOLS_ROOT,
            env=_environment(harness),
            log_path=log_path,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        return_code = child.process.wait(timeout=TIMEOUT_SECONDS)
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while _group_exists(child) and time.monotonic() < deadline:
            time.sleep(0.01)
        if _group_exists(child):
            raise RuntimeError(f"Completed CLI process group {child.pgid} did not drain")
        child.drained = True
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
    _raise_failures(primary, cleanup_failures, log_path)
    assert return_code is not None
    return return_code


def _run_cli_forbid_send(
    harness: _Harness,
    scenario_path: Path,
    run_id: str,
    action: str,
    marker_path: Path,
    log_path: Path,
) -> int:
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    primary: BaseException | None = None
    return_code: int | None = None
    try:
        child = _launch(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                str(CRASH_RUNNER),
                "--action",
                action,
                "--phase",
                "forbid_send",
                "--gate",
                str(marker_path),
                "--",
                *_production_arguments(scenario_path, run_id),
            ],
            cwd=TOOLS_ROOT,
            env=_environment(harness),
            log_path=log_path,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        return_code = child.process.wait(timeout=TIMEOUT_SECONDS)
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while _group_exists(child) and time.monotonic() < deadline:
            time.sleep(0.01)
        if _group_exists(child):
            raise RuntimeError(f"Completed CLI process group {child.pgid} did not drain")
        child.drained = True
        assert not marker_path.exists(), "Restart attempted the forbidden target request"
    except BaseException as exc:
        primary = exc
    finally:
        ownership.close()
    _raise_failures(primary, cleanup_failures, log_path)
    assert return_code is not None
    return return_code


def _wait_for_judging_gate(
    runtime: IsolatedRuntime,
    gate_path: Path,
    request: concurrent.futures.Future[httpx.Response],
) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if gate_path.exists():
            value = json.loads(gate_path.read_text(encoding="utf-8"))
            assert isinstance(value, dict)
            return value
        if request.done():
            response = request.result()
            raise AssertionError(
                "Court ruling request completed before the judging persistence gate: "
                f"HTTP {response.status_code} {response.text}"
            )
        court = runtime.processes.get("court")
        if court is None or court.poll() is not None:
            raise AssertionError(
                "Gated Court exited before authoritative judging persistence; "
                f"log: {runtime.log_paths['court']}"
            )
        time.sleep(0.05)
    raise TimeoutError(f"Court did not persist judging; log: {runtime.log_paths['court']}")


def _seed_authoritative_judging(
    harness: _Harness,
    *,
    dispute_id: str,
    task_id: str,
    gate_path: Path,
) -> dict[str, Any]:
    runtime = harness.runtime
    previous_module = runtime_module.SERVICE_MODULES["court"]
    previous_pythonpath = os.environ.get("PYTHONPATH")
    previous_gate = os.environ.get("MP05_JUDGING_GATE")
    request_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    request: concurrent.futures.Future[httpx.Response] | None = None
    gated_started = False
    primary: BaseException | None = None
    cleanup_failures: list[BaseException] = []
    gate: dict[str, Any] | None = None
    runtime.stop_service("court")
    try:
        runtime_module.SERVICE_MODULES["court"] = "mp05_demo_judging_phase_server:create_app"
        existing_path = "" if previous_pythonpath is None else previous_pythonpath
        os.environ["PYTHONPATH"] = (
            f"{JUDGING_SERVER.parent}{os.pathsep}{existing_path}"
            if existing_path
            else str(JUDGING_SERVER.parent)
        )
        os.environ["MP05_JUDGING_GATE"] = str(gate_path)
        runtime.start_service("court")
        gated_started = True

        platform_config = yaml.safe_load(harness.config_path.read_text(encoding="utf-8"))[
            "platform"
        ]
        private_key, public_key = load_keypair(
            "platform",
            runtime.run_dir / "keys",
        )
        expected_public_key = f"ed25519:{public_key_to_b64(public_key)}"
        token_ttl_seconds = platform_config["token_ttl_seconds"]
        assert type(token_ttl_seconds) is int
        registry = httpx.get(
            f"{runtime.service_url('identity')}/agents",
            timeout=TIMEOUT_SECONDS,
        )
        registry.raise_for_status()
        exact_platform: list[dict[str, Any]] = []
        for summary in registry.json()["agents"]:
            if summary.get("name") != "Platform":
                continue
            candidate = httpx.get(
                f"{runtime.service_url('identity')}/agents/{summary['agent_id']}",
                timeout=TIMEOUT_SECONDS,
            )
            candidate.raise_for_status()
            value = candidate.json()
            if value.get("public_key") == expected_public_key:
                exact_platform.append(value)
        assert len(exact_platform) == 1
        token = create_jws(
            {"action": "trigger_ruling", "dispute_id": dispute_id},
            private_key,
            kid=exact_platform[0]["agent_id"],
            audience="court",
            resource=f"POST /disputes/{dispute_id}/rule",
            token_ttl_seconds=token_ttl_seconds,
        )
        request = request_pool.submit(
            httpx.post,
            f"{runtime.service_url('court')}/disputes/{dispute_id}/rule",
            json={"token": token},
            timeout=TIMEOUT_SECONDS,
        )
        gate = _wait_for_judging_gate(runtime, gate_path, request)
        assert gate["phase"] == "after_judging_persisted"
        assert gate["dispute_id"] == dispute_id and gate["task_id"] == task_id
    except BaseException as exc:
        primary = exc
    finally:
        if gated_started:
            _capture_cleanup(cleanup_failures, runtime.stop_service, "court")
        runtime_module.SERVICE_MODULES["court"] = previous_module
        if previous_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = previous_pythonpath
        if previous_gate is None:
            os.environ.pop("MP05_JUDGING_GATE", None)
        else:
            os.environ["MP05_JUDGING_GATE"] = previous_gate
        if request is not None:
            try:
                request.result(timeout=TIMEOUT_SECONDS)
            except (httpx.HTTPError, concurrent.futures.TimeoutError):
                pass
            except BaseException as exc:
                cleanup_failures.append(exc)
        request_pool.shutdown(wait=True)
        _capture_cleanup(cleanup_failures, runtime.start_service, "court")
    _raise_failures(primary, cleanup_failures, gate_path)
    assert gate is not None
    return gate


def _database_path(runtime: IsolatedRuntime) -> Path:
    config = yaml.safe_load(runtime.gateway_config_path.read_text(encoding="utf-8"))
    return Path(config["database"]["path"])


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]


def _projection(runtime: IsolatedRuntime) -> dict[str, object]:
    with sqlite3.connect(f"file:{_database_path(runtime)}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        projection: dict[str, object] = {
            table: _rows(connection, table) for table in PROJECTION_TABLES
        }
        issuance = connection.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bank_transactions WHERE type='credit'"
        ).fetchone()
        balances = connection.execute(
            "SELECT COALESCE(SUM(balance),0) FROM bank_accounts"
        ).fetchone()
        locked = connection.execute(
            "SELECT COALESCE(SUM(amount),0) FROM bank_escrow WHERE status='locked'"
        ).fetchone()
        assert issuance is not None and balances is not None and locked is not None
        projection["conservation"] = {
            "issuance": int(issuance[0]),
            "balances": int(balances[0]),
            "locked": int(locked[0]),
        }
        return projection


def _new_rows(
    current: dict[str, object], baseline: dict[str, object], table: str
) -> list[dict[str, Any]]:
    observed = current[table]
    original = baseline[table]
    assert isinstance(observed, list) and isinstance(original, list)
    return [row for row in observed if row not in original]


def _new_rows_by_key(
    current: dict[str, object],
    baseline: dict[str, object],
    table: str,
    key: str,
) -> list[dict[str, Any]]:
    observed = current[table]
    original = baseline[table]
    assert isinstance(observed, list) and isinstance(original, list)
    original_keys = {row[key] for row in original}
    return [row for row in observed if row[key] not in original_keys]


def _table_delta(
    current: dict[str, object], baseline: dict[str, object], table: str
) -> dict[str, list[dict[str, Any]]]:
    observed = current[table]
    original = baseline[table]
    assert isinstance(observed, list) and isinstance(original, list)
    keys = PRIMARY_KEYS[table]

    def identity(row: dict[str, Any]) -> tuple[object, ...]:
        return tuple(row[key] for key in keys)

    observed_by_key = {identity(row): row for row in observed}
    original_by_key = {identity(row): row for row in original}
    return {
        "added": [observed_by_key[key] for key in observed_by_key.keys() - original_by_key.keys()],
        "changed": [
            observed_by_key[key]
            for key in observed_by_key.keys() & original_by_key.keys()
            if observed_by_key[key] != original_by_key[key]
        ],
        "removed": [
            original_by_key[key] for key in original_by_key.keys() - observed_by_key.keys()
        ],
    }


def _projection_delta(
    current: dict[str, object], baseline: dict[str, object]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    return {table: _table_delta(current, baseline, table) for table in PROJECTION_TABLES}


def _assert_exact_projection_delta(
    delta: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    action: str,
    phase: str,
    intent: dict[str, object],
) -> None:
    expected_counts = _expected_added_counts(action, phase)
    assert set(delta) == set(PROJECTION_TABLES) == set(expected_counts)
    for table, table_delta in delta.items():
        assert table_delta["removed"] == [], f"{table} lost baseline rows"
        assert table_delta["changed"] == [], f"{table} changed baseline rows"
        assert len(table_delta["added"]) == expected_counts[table], table

    added = {table: value["added"] for table, value in delta.items()}
    events = added["events"]
    events_by_id = {row["event_id"]: row for row in events}
    assert Counter(row["event_type"] for row in events) == _expected_event_types(action, phase)

    agents = {row["agent_id"]: row for row in added["identity_agents"]}
    accounts = {row["account_id"]: row for row in added["bank_accounts"]}
    assert set(accounts) == set(agents)
    for agent_id in agents:
        assert any(
            event["event_type"] == "agent.registered" and event["agent_id"] == agent_id
            for event in events
        )
        assert any(
            event["event_type"] == "account.created" and event["agent_id"] == agent_id
            for event in events
        )

    tasks = added["board_tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    task_id = task["task_id"]
    assert intent["task_id"] == task_id
    assert task["poster_id"] in agents and task["worker_id"] in agents
    assert task["reward"] == REWARD
    expected_status = {
        "upload_asset": "accepted",
        "file_claim": "submitted",
        "submit_rebuttal": "disputed",
        "trigger_ruling": "ruled" if phase == "after_response" else "disputed",
        "feedback": "approved",
    }[action]
    assert task["status"] == expected_status

    escrow = added["bank_escrow"][0]
    assert escrow["task_id"] == task_id
    assert escrow["escrow_id"] == task["escrow_id"]
    assert escrow["payer_account_id"] == task["poster_id"]
    assert escrow["amount"] == REWARD
    assert escrow["status"] == {
        "feedback": "released",
        "trigger_ruling": "split" if phase == "after_response" else "locked",
    }.get(action, "locked")

    publication = added["board_task_publications"][0]
    assert publication["task_id"] == task_id
    assert publication["actor_id"] == task["poster_id"]
    assert publication["escrow_id"] == escrow["escrow_id"]
    assert publication["creation_event_id"] in events_by_id
    assert publication["lock_receipt_event_id"] in events_by_id
    bid = added["board_bids"][0]
    assert bid["task_id"] == task_id
    assert bid["bidder_id"] == task["worker_id"]
    assert bid["bid_id"] == task["accepted_bid_id"]
    assert bid["amount"] == BID_AMOUNT

    for asset in added["board_assets"]:
        assert asset["task_id"] == task_id
        assert asset["uploader_id"] == task["worker_id"]
    operations = added["board_task_operations"]
    expected_commands = Counter({"submit_bid": 1, "accept_bid": 1})
    if action != "upload_asset":
        expected_commands["submit_deliverable"] += 1
    if action in {"submit_rebuttal", "trigger_ruling"}:
        expected_commands["dispute_task"] += 1
    if action == "feedback":
        expected_commands["approve_task"] += 1
    if action == "trigger_ruling" and phase == "after_response":
        expected_commands["record_ruling"] += 1
    assert Counter(row["command"] for row in operations) == expected_commands
    assert all(row["task_id"] == task_id for row in operations)
    assert all(row["event_id"] in events_by_id for row in operations)

    transactions = {row["tx_id"]: row for row in added["bank_transactions"]}
    receipts = {row["operation_id"]: row for row in added["bank_operation_receipts"]}
    legs = added["bank_operation_legs"]
    expected_receipts = Counter({"credit": 1, "escrow_lock": 1})
    if action == "feedback":
        expected_receipts["escrow_release"] += 1
    if action == "trigger_ruling" and phase == "after_response":
        expected_receipts["escrow_split"] += 1
    assert Counter(row["operation_kind"] for row in receipts.values()) == expected_receipts
    assert all(row["event_id"] in events_by_id for row in receipts.values())
    assert all(row["event_id"] in events_by_id for row in transactions.values())
    assert all(row["account_id"] in accounts for row in transactions.values())
    for leg in legs:
        assert leg["operation_id"] in receipts
        transaction_id = leg["transaction_id"]
        assert transaction_id in transactions
        transaction = transactions[transaction_id]
        assert transaction["account_id"] == leg["account_id"]
        assert transaction["amount"] == leg["amount"]
        assert transaction["balance_after"] == leg["balance_after"]
    assert {row["transaction_id"] for row in legs} == set(transactions)
    assert (
        sum(row["balance"] for row in accounts.values())
        + (escrow["amount"] if escrow["status"] == "locked" else 0)
        == FUNDING_AMOUNT
    )

    claims = added["court_claims"]
    rebuttals = added["court_rebuttals"]
    rulings = added["court_rulings"]
    feedback = added["reputation_feedback"]
    for claim in claims:
        assert claim["task_id"] == task_id
        assert claim["claimant_id"] == task["poster_id"]
        assert claim["respondent_id"] == task["worker_id"]
    for rebuttal in rebuttals:
        assert claims and rebuttal["claim_id"] == claims[0]["claim_id"]
        assert rebuttal["agent_id"] == task["worker_id"]
    for ruling in rulings:
        assert claims and ruling["claim_id"] == claims[0]["claim_id"]
        assert ruling["task_id"] == task_id
        assert ruling["worker_pct"] == 50
    for item in feedback:
        assert item["task_id"] == task_id
        assert item["to_agent_id"] in agents
    if claims:
        assert any(event["event_type"] == "claim.filed" for event in events)
    if rebuttals:
        assert any(event["event_type"] == "rebuttal.submitted" for event in events)
    if rulings:
        assert any(event["event_type"] == "ruling.delivered" for event in events)


def _assert_authoritative_judging_delta(
    before: dict[str, object],
    after: dict[str, object],
    *,
    dispute_id: str,
) -> None:
    delta = _projection_delta(after, before)
    for table, table_delta in delta.items():
        assert table_delta["removed"] == [], table
        if table == "court_claims":
            assert table_delta["added"] == []
            assert len(table_delta["changed"]) == 1
            claim = table_delta["changed"][0]
            assert claim["claim_id"] == dispute_id
            assert claim["status"] == "judging"
        elif table == "events":
            assert table_delta["changed"] == []
            assert len(table_delta["added"]) == 1
            event = table_delta["added"][0]
            assert event["event_source"] == "court"
            assert event["event_type"] == "claim.status_changed"
            assert json.loads(event["payload"]) == {
                "claim_id": dispute_id,
                "status": "judging",
            }
        else:
            assert table_delta["added"] == [], table
            assert table_delta["changed"] == [], table


def _step_record(state_directory: Path, run_id: str, index: int) -> dict[str, Any]:
    path = state_directory / "runs" / run_id / "steps" / f"{index:06d}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _key_hashes(state_directory: Path, run_id: str) -> dict[str, str]:
    key_root = state_directory / "runs" / run_id / "keys"
    return {
        str(path.relative_to(key_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(key_root.rglob("*"))
        if path.is_file()
    }


def _write_audit(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _close_then_audit(
    *,
    ownership: contextlib.ExitStack,
    cleanup_failures: list[BaseException],
    primary: BaseException | None,
    audit_path: Path,
    audit: dict[str, object],
) -> None:
    ownership.close()
    try:
        _write_audit(audit_path, audit)
    except BaseException as exc:
        cleanup_failures.append(exc)
    _raise_failures(primary, cleanup_failures, audit_path)


def _target_effect_rows(
    projection: dict[str, object],
    baseline: dict[str, object],
    action: str,
    intent: dict[str, object],
) -> list[dict[str, Any]]:
    rows = _new_rows(projection, baseline, EFFECT_TABLE[action])
    if action == "submit_rebuttal":
        return [row for row in rows if row.get("claim_id") == intent["dispute_id"]]
    return [row for row in rows if row.get("task_id") == intent["task_id"]]


def _assert_conservation(projection: dict[str, object]) -> None:
    conservation = projection["conservation"]
    assert isinstance(conservation, dict)
    assert conservation["issuance"] == conservation["balances"] + conservation["locked"]


@pytest.mark.parametrize("action", sorted(EFFECT_TABLE))
@pytest.mark.parametrize("phase", ["before_send", "after_response"])
def test_demo_other_action_restart_uses_only_authoritative_evidence(
    other_action_harness: _Harness,
    tmp_path: Path,
    action: str,
    phase: str,
) -> None:
    """A process cut cannot duplicate an effect or invent completion evidence."""
    harness = other_action_harness
    run_id = f"run-{uuid.uuid4()}"
    case = tmp_path / f"{action}-{phase}-{run_id}"
    case.mkdir()
    scenario_path, target_index = _write_scenario(case, action, run_id)
    gate_path = case / "gate.json"
    crash_log = case / "crash.log"
    restart_log = case / "restart.log"
    replay_log = case / "replay.log"
    audit_path = case / "audit.json"
    baseline = _projection(harness.runtime)
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []
    primary: BaseException | None = None
    audit: dict[str, object] = {
        "action": action,
        "phase": phase,
        "run_id": run_id,
        "classification": "setup_or_validation_pending",
        "crash_log": str(crash_log),
        "restart_log": str(restart_log),
        "replay_log": str(replay_log),
    }
    try:
        child = _launch_crash(
            harness,
            scenario_path,
            run_id,
            action,
            phase,
            gate_path,
            crash_log,
            ownership=ownership,
            cleanup_failures=cleanup_failures,
        )
        gate = _wait_for_gate(child, gate_path, crash_log)
        audit["classification"] = "target_gate_reached"
        audit["gate"] = gate
        dispatching = _step_record(harness.state_directory, run_id, target_index)
        assert dispatching["action"] == action
        assert dispatching["state"] == "dispatching"
        assert dispatching["result"] is None
        intent = dispatching["intent"]
        assert isinstance(intent, dict)
        key_hashes = _key_hashes(harness.state_directory, run_id)
        credential_hashes = {
            name: digest
            for name, digest in key_hashes.items()
            if Path(name).suffix in {".key", ".pub"}
        }
        assert len(credential_hashes) == 4
        _kill_at_gate(child, gate)
        after_crash = _projection(harness.runtime)
        after_crash_delta = _projection_delta(after_crash, baseline)
        audit["after_crash_delta"] = after_crash_delta
        _assert_exact_projection_delta(
            after_crash_delta,
            action=action,
            phase=phase,
            intent=intent,
        )
        effect_rows = _target_effect_rows(after_crash, baseline, action, intent)
        assert len(effect_rows) == (1 if phase == "after_response" else 0)
        new_agents = _new_rows_by_key(after_crash, baseline, "identity_agents", "agent_id")
        new_accounts = _new_rows_by_key(after_crash, baseline, "bank_accounts", "account_id")
        assert len(new_agents) == 2
        assert {row["account_id"] for row in new_accounts} == {
            row["agent_id"] for row in new_agents
        }
        _assert_conservation(after_crash)

        restart_baseline = after_crash
        forbid_restart = action == "trigger_ruling" and phase == "before_send"
        if forbid_restart:
            dispute_id = intent["dispute_id"]
            task_id = intent["task_id"]
            assert isinstance(dispute_id, str) and isinstance(task_id, str)
            judging_gate = _seed_authoritative_judging(
                harness,
                dispute_id=dispute_id,
                task_id=task_id,
                gate_path=case / "judging-gate.json",
            )
            audit["judging_gate"] = judging_gate
            restart_baseline = _projection(harness.runtime)
            _assert_authoritative_judging_delta(
                after_crash,
                restart_baseline,
                dispute_id=dispute_id,
            )
            _assert_conservation(restart_baseline)
            restart_code = _run_cli_forbid_send(
                harness,
                scenario_path,
                run_id,
                action,
                case / "forbidden-restart-send.json",
                restart_log,
            )
        else:
            restart_code = _run_cli(harness, scenario_path, run_id, restart_log)
        terminal = _step_record(harness.state_directory, run_id, target_index)
        after_restart = _projection(harness.runtime)
        assert after_restart == restart_baseline
        assert _key_hashes(harness.state_directory, run_id) == key_hashes
        assert _group_exists(harness.sentinel)

        should_adopt = action not in NONREPLAYABLE and phase == "after_response"
        assert terminal["state"] == ("completed" if should_adopt else "attention_required")
        assert restart_code == (0 if should_adopt else 1)
        if should_adopt:
            assert gate["response"] is not None
            response = terminal["result"]["response"]
            assert response == gate["response"]
        else:
            assert terminal["result"]["reason"] in {
                "ambiguous_dispatch",
                "ambiguous_claim",
                "ambiguous_rebuttal",
                "ambiguous_ruling",
            }

        if forbid_restart:
            replay_code = _run_cli_forbid_send(
                harness,
                scenario_path,
                run_id,
                action,
                case / "forbidden-replay-send.json",
                replay_log,
            )
        else:
            replay_code = _run_cli(harness, scenario_path, run_id, replay_log)
        after_replay = _projection(harness.runtime)
        assert after_replay == after_restart
        assert replay_code == (0 if should_adopt else 1)
        assert _step_record(harness.state_directory, run_id, target_index) == terminal
        assert _key_hashes(harness.state_directory, run_id) == key_hashes
        assert _group_exists(harness.sentinel)
        _assert_conservation(after_replay)

        audit.update(
            {
                "classification": "target_behavior_verified",
                "restart_code": restart_code,
                "replay_code": replay_code,
                "terminal": terminal,
                "effect_count": len(effect_rows),
                "new_event_count": len(_new_rows(after_replay, baseline, "events")),
                "conservation": after_replay["conservation"],
            }
        )
    except BaseException as exc:
        primary = exc
        audit["error_type"] = type(exc).__name__
        audit["error"] = str(exc)
    _close_then_audit(
        ownership=ownership,
        cleanup_failures=cleanup_failures,
        primary=primary,
        audit_path=audit_path,
        audit=audit,
    )


def test_cleanup_precedes_audit_and_preserves_all_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An audit I/O failure cannot bypass cleanup or hide earlier failures."""
    events: list[str] = []
    ownership = contextlib.ExitStack()
    cleanup_failures: list[BaseException] = []

    def fail_cleanup() -> None:
        events.append("cleanup")
        raise RuntimeError("injected cleanup failure")

    def fail_audit(_path: Path, _value: dict[str, object]) -> None:
        events.append("audit")
        raise OSError("injected audit write failure")

    ownership.callback(_capture_cleanup, cleanup_failures, fail_cleanup)
    monkeypatch.setattr(
        "test_mp05_demo_other_action_restart._write_audit",
        fail_audit,
    )
    primary = AssertionError("injected target failure")
    evidence_path = tmp_path / "audit.json"

    with pytest.raises(BaseExceptionGroup) as exc_info:
        _close_then_audit(
            ownership=ownership,
            cleanup_failures=cleanup_failures,
            primary=primary,
            audit_path=evidence_path,
            audit={"classification": "injected"},
        )

    assert events == ["cleanup", "audit"]
    assert [str(error) for error in exc_info.value.exceptions] == [
        "injected target failure",
        "injected cleanup failure",
        "injected audit write failure",
    ]
