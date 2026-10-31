"""Manage immutable recovery artifacts (backup, verify, finalize, restore)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel, ValidationError
from service_commons.recovery_contract import RecoveryInventoryV1, TargetBindingsV1

from db_gateway_service import recovery


class _InputError(ValueError):
    """Invalid CLI input without retaining potentially sensitive input values."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _backup_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--work-root", type=Path, required=True)


def _parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    actions = backup.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    _backup_options(create)
    create.add_argument("--inventory", type=Path, required=True)
    for name in ("verify", "finalize"):
        _backup_options(actions.add_parser(name))

    restore = commands.add_parser("restore")
    _backup_options(restore)
    restore.add_argument("--target-bindings", type=Path, required=True)
    restore.add_argument("--target-root", type=Path, required=True)
    for name in ("verify-restore", "finalize-restore"):
        check = commands.add_parser(name)
        check.add_argument("--target-root", type=Path, required=True)
        check.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args(arguments)


def _load_contract[Model: BaseModel](path: Path, model: type[Model], reason: str) -> Model:
    try:
        return model.model_validate_json(path.read_bytes())
    except ValidationError:
        # Pydantic's exception text can include rejected input values. Never echo it.
        raise _InputError(reason) from None


def _execute_backup(
    args: argparse.Namespace,
) -> tuple[str, recovery.PublicationResult | recovery.VerificationResult]:
    if args.action == "create":
        inventory = _load_contract(args.inventory, RecoveryInventoryV1, "invalid_inventory")
        return "backup.create", recovery.create_backup(
            inventory,
            backup_root=args.backup_root,
            backup_id=args.backup_id,
            work_root=args.work_root,
        )
    if args.action == "verify":
        return "backup.verify", recovery.verify_backup(
            backup_root=args.backup_root,
            backup_id=args.backup_id,
            work_root=args.work_root,
        )
    if args.action == "finalize":
        return "backup.finalize", recovery.finalize_backup(
            backup_root=args.backup_root,
            backup_id=args.backup_id,
            work_root=args.work_root,
        )
    raise ValueError("Unrecognized backup action")


def _execute(
    args: argparse.Namespace,
) -> tuple[str, recovery.PublicationResult | recovery.VerificationResult]:
    if args.command == "backup":
        return _execute_backup(args)
    if args.command == "restore":
        bindings = _load_contract(args.target_bindings, TargetBindingsV1, "invalid_target_bindings")
        return "restore", recovery.restore_backup(
            backup_root=args.backup_root,
            backup_id=args.backup_id,
            target_bindings=bindings,
            target_root=args.target_root,
            work_root=args.work_root,
        )
    if args.command == "verify-restore":
        return "verify-restore", recovery.verify_restore(
            target_root=args.target_root, work_root=args.work_root
        )
    if args.command == "finalize-restore":
        return "finalize-restore", recovery.finalize_restore(
            target_root=args.target_root, work_root=args.work_root
        )
    raise ValueError("Unrecognized recovery command")


def _report_error(reason: str) -> int:
    print(
        json.dumps({"error": reason, "message": "Recovery operation failed"}, sort_keys=True),
        file=sys.stderr,
    )
    return 1


def _result_exit_code(
    result: recovery.PublicationResult | recovery.VerificationResult,
) -> int:
    if isinstance(result, recovery.PublicationResult):
        return 0 if result.state is recovery.PublicationState.COMPLETE else 1
    return 0 if result.artifact_verified is True else 1


def main(arguments: list[str]) -> int:
    """Run one explicit operation; report artifact state without claiming readiness."""
    args = _parse_arguments(arguments)
    try:
        operation, result = _execute(args)
    except _InputError as exc:
        return _report_error(exc.reason)
    except recovery.RecoveryError as exc:
        return _report_error(exc.reason)
    except OSError:
        return _report_error("filesystem_error")

    print(
        json.dumps(
            {"operation": operation, "result": result.model_dump(mode="json")}, sort_keys=True
        )
    )
    return _result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
