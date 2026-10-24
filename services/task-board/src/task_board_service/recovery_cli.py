"""Signed operator diagnostics and retry controls for MP-08 recovery."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Never

from pydantic import ValidationError
from service_auth.gateway import GatewayRequestSigner
from service_commons.config import ConfigurationError, load_yaml_config

from task_board_service.config import Settings
from task_board_service.services.task_db_client import TaskDbClient

_IMMUTABLE_IDENTITY_FIELDS = (
    "effect_operation_id",
    "task_id",
    "source_kind",
    "source_id",
    "effect_kind",
)


class _CliError(Exception):
    """Internal, deliberately detail-free CLI failure."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise _CliError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="task-board-recovery")
    parser.add_argument("--config", required=True)
    resources = parser.add_subparsers(dest="resource", required=True)
    effects = resources.add_parser("effects")
    commands = effects.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list")
    list_parser.add_argument(
        "--state",
        required=True,
        choices=("all", "pending", "leased", "dead_letter", "completed", "integrity_blocked"),
    )
    list_parser.add_argument("--after", required=True)
    list_parser.add_argument("--limit", required=True, type=int)

    for command in ("show", "retry"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("--operation-id", required=True)
    return parser


def _load_client(config_path: Path) -> TaskDbClient:
    try:
        raw = load_yaml_config(config_path)
        settings = Settings.model_validate(raw)
        signer = GatewayRequestSigner(
            settings.db_gateway.signing,
            config_dir=config_path.parent,
        )
    except (
        OSError,
        UnicodeError,
        ConfigurationError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise _CliError("invalid configuration") from exc
    return TaskDbClient(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
        signer=signer,
    )


def _delivery(body: dict[str, Any]) -> dict[str, Any]:
    delivery = body.get("delivery")
    if not isinstance(delivery, dict):
        raise _CliError("invalid diagnostic")
    return delivery


def _execute(client: TaskDbClient, arguments: argparse.Namespace) -> dict[str, Any]:
    command = arguments.command
    if command == "list":
        return client.list_task_effect_deliveries(
            arguments.state,
            arguments.after,
            arguments.limit,
        )
    if command == "show":
        return client.get_task_effect_delivery(arguments.operation_id)
    if command != "retry":
        raise _CliError("unsupported command")

    before = client.get_task_effect_delivery(arguments.operation_id)
    before_delivery = _delivery(before)
    if before_delivery.get("delivery_state") != "dead_letter":
        raise _CliError("delivery is not retryable")
    after = client.retry_task_effect_delivery(arguments.operation_id)
    after_delivery = _delivery(after)
    if any(
        after_delivery.get(field) != before_delivery.get(field)
        for field in _IMMUTABLE_IDENTITY_FIELDS
    ):
        raise _CliError("retry identity changed")
    return after


def main(argv: list[str]) -> int:
    """Run one explicit signed recovery diagnostic command."""
    client: TaskDbClient | None = None
    try:
        arguments = _parser().parse_args(argv)
        client = _load_client(Path(arguments.config))
        result = _execute(client, arguments)
        sys.stdout.write(
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        )
        return 0
    except (Exception, SystemExit):
        sys.stderr.write("task-board-recovery failed\n")
        return 1
    finally:
        if client is not None:
            client.close()


def cli() -> int:
    """Read the explicit process argument vector for the installed command."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(cli())
