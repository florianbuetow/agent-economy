"""Owned public-SDK actor for MP-08 real-process cancellation histories."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from service_auth import AgentFactory
from service_auth.financial_identity import operation_id_for


def _publish(path: Path, value: dict[str, object]) -> None:
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, path)
    finally:
        staged.unlink(missing_ok=True)


async def _run(arguments: argparse.Namespace) -> None:
    factory = AgentFactory(config_path=arguments.config)
    platform = factory.platform_agent()
    poster = factory.user_agent("operator")
    worker = factory.create_agent("alice")
    try:
        await platform.register()
        await poster.register()
        await worker.register()
        assert poster.agent_id is not None
        assert worker.agent_id is not None
        await platform.create_account(
            poster.agent_id,
            arguments.reward,
            operation_id=operation_id_for(
                "mp08-process-funding",
                (arguments.operation_id, poster.agent_id),
            ),
        )
        await worker.create_account()
        intent = poster.prepare_task_posting(
            title="MP-08 cancellation restart",
            spec="Recover the exact cancellation refund after Task Board process death.",
            reward=arguments.reward,
            bidding_deadline_seconds=3600,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=3600,
            review_deadline_seconds=3600,
        )
        publication = await poster.submit_task_posting(intent)
        assert publication.status_code == 201
        result = publication.body["result"]
        assert isinstance(result, dict)
        assert result["status"] == "open"
        command_version = int(result["lifecycle_version"])
        target_status = "cancelled"
        recipient_id = poster.agent_id
        ruling_id: str | None = None
        if arguments.action in {"approve", "ruling"}:
            bid = await worker.submit_bid(
                intent.task_id,
                90,
                operation_id=operation_id_for(
                    "mp08-process-bid", (intent.task_id, worker.agent_id)
                ),
                expected_lifecycle_version=command_version,
            )
            bid_id = bid.body["bid_id"]
            assert isinstance(bid_id, str)
            accepted = await poster.accept_bid(
                intent.task_id,
                bid_id,
                operation_id=operation_id_for("mp08-process-accept", (intent.task_id, bid_id)),
                expected_lifecycle_version=command_version,
            )
            command_version = int(accepted.body["lifecycle_version"])
            await worker.upload_asset(intent.task_id, "result.txt", b"MP-08 process result\n")
            submitted = await worker.submit_deliverable(
                intent.task_id,
                operation_id=operation_id_for(
                    "mp08-process-submit", (intent.task_id, worker.agent_id)
                ),
                expected_lifecycle_version=command_version,
            )
            command_version = int(submitted.body["lifecycle_version"])
            target_status = "approved"
            recipient_id = worker.agent_id
            if arguments.action == "ruling":
                disputed = await poster.dispute_task(
                    intent.task_id,
                    "The result requires adjudication",
                    operation_id=operation_id_for(
                        "mp08-process-dispute", (intent.task_id, poster.agent_id)
                    ),
                    expected_lifecycle_version=command_version,
                )
                command_version = int(disputed.body["lifecycle_version"])
                ruling_id_value = disputed.body["dispute_id"]
                assert isinstance(ruling_id_value, str)
                ruling_id = ruling_id_value
                target_status = "ruled"
        _publish(
            arguments.ready,
            {
                "pid": os.getpid(),
                "pgid": os.getpgrp(),
                "sid": os.getsid(0),
                "poster_id": poster.agent_id,
                "worker_id": worker.agent_id,
                "platform_id": platform.agent_id,
                "task_id": intent.task_id,
                "escrow_id": result["escrow_id"],
                "reward": arguments.reward,
                "operation_id": arguments.operation_id,
                "lock_operation_id": intent.lock_operation_id,
                "source_lifecycle_version": command_version,
                "target_lifecycle_version": command_version + 1,
                "target_status": target_status,
                "recipient_id": recipient_id,
                "dispute_id": ruling_id,
                "action": arguments.action,
            },
        )
        while not arguments.trigger.exists():
            await asyncio.sleep(arguments.poll_seconds)
        try:
            if arguments.action == "cancel":
                response = await poster.cancel_task(
                    intent.task_id,
                    operation_id=arguments.operation_id,
                    expected_lifecycle_version=command_version,
                )
            elif arguments.action == "approve":
                response = await poster.approve_task(
                    intent.task_id,
                    operation_id=arguments.operation_id,
                    expected_lifecycle_version=command_version,
                )
            else:
                response = await platform.record_ruling(
                    intent.task_id,
                    {
                        "action": "record_ruling",
                        "operation_id": arguments.operation_id,
                        "expected_lifecycle_version": command_version,
                        "task_id": intent.task_id,
                        "ruling_id": ruling_id,
                        "ruling_summary": "Split the escrow according to the ruling",
                        "worker_pct": 60,
                    },
                )
        except BaseException as exc:
            _publish(
                arguments.result,
                {
                    "outcome": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        else:
            if isinstance(response, dict):
                status_code = 200
                body = response
            else:
                status_code = response.status_code
                body = response.body
            _publish(
                arguments.result,
                {
                    "outcome": "response",
                    "status_code": status_code,
                    "body": body,
                },
            )
        # Retain the exact owned actor group until the parent drains it. Letting
        # the uv launcher exit first creates an unobservable dying-group window.
        await asyncio.Event().wait()
    finally:
        await worker.close()
        await poster.close()
        await platform.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--trigger", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--action", choices=("cancel", "approve", "ruling"), required=True)
    parser.add_argument("--reward", type=int, required=True)
    parser.add_argument("--poll-seconds", type=float, required=True)
    arguments = parser.parse_args()
    if arguments.reward <= 0 or arguments.poll_seconds <= 0:
        raise ValueError("reward and poll-seconds must be positive")
    asyncio.run(_run(arguments))


if __name__ == "__main__":
    main()
