"""Test-only TaskDbClient boundary backed by production Gateway persistence.

No HTTP authentication is simulated: caller_kid is explicit test authority.
The fixture owns the database path; close releases this adapter's connection only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from db_gateway_service.routers.helpers import validate_constraints
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.input_manifest_reader import read_task_input_manifest
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.services.task_publication_writer import TaskPublicationWriter
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_publication_requests import parse_task_publication_request
from db_gateway_service.task_time import epoch_us_to_utc_text
from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from db_gateway_service.board_authority import BoardAuthority


class GatewayTaskStore(TaskDbClient):
    """Reuse the real Board client with an isolated synchronous storage boundary.

    The caller supplies all paths, authority, database settings, and time. Supporting
    identity, Bank, and Court facts must be persisted explicitly through ``writer``.
    Neither construction nor a storage method invents missing fixture history.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        busy_timeout_ms: int,
        journal_mode: str,
        schema_sql: str | None,
        caller_kid: str,
        authority: BoardAuthority,
        clock_us: Callable[[], int],
    ) -> None:
        if not db_path.is_absolute():
            raise ValueError("Test database path must be an explicit absolute file path")
        if not caller_kid or caller_kid.strip() != caller_kid:
            raise ValueError("Test caller KID must be a nonblank canonical string")
        self._writer = DbWriter(
            str(db_path),
            busy_timeout_ms,
            journal_mode,
            schema_sql,
            current_task_board_caller_kid=caller_kid,
        )
        try:
            self._reader = DbReader(self._writer.connection)
            self._operations = TaskOperationWriter(
                self._writer.connection,
                clock_us,
                authority.deadline_actor_id,
                authority.platform_public_key,
                current_task_board_caller_kid=caller_kid,
            )
            self._publications = TaskPublicationWriter(
                self._writer.connection,
                clock_us=clock_us,
            )
        except BaseException:
            self._writer.close()
            raise
        self._caller_kid = caller_kid
        self._clock_us = clock_us
        self._closed = False

    @property
    def writer(self) -> DbWriter:
        """Expose real writer APIs for explicit fixture facts and external effects."""
        self._require_open()
        return self._writer

    @property
    def reader(self) -> DbReader:
        """Expose real read APIs for fixture evidence without inventing projections."""
        self._require_open()
        return self._reader

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Gateway test store is closed")

    def _now(self) -> str:
        self._require_open()
        return epoch_us_to_utc_text(self._clock_us())

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        """Translate only the existing client boundary; never make a network call."""
        self._require_open()
        try:
            if method == "GET":
                status, result = self._read(path, {} if params is None else params)
            elif method == "POST" and json is not None:
                status, result = self._write(path, json)
            else:
                raise AssertionError(f"Unsupported test store request: {method} {path}")
        except ServiceError as error:
            status = error.status_code
            result = {"error": error.error, "message": error.message, "details": error.details}
        return httpx.Response(
            status,
            json=result,
            request=httpx.Request(method, f"http://test-gateway.invalid{path}"),
        )

    def _read(self, path: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/board/posting-recovery":
            return 200, self._publications.recovery(
                params["after"], params["limit"], self._caller_kid
            )
        if path == "/board/tasks":
            return 200, {
                "tasks": self._reader.list_tasks(
                    params.get("status"),
                    params.get("poster_id"),
                    params.get("worker_id"),
                    params.get("limit"),
                    params.get("offset"),
                )
            }
        if path == "/board/tasks/count":
            return 200, {"count": self._reader.count_tasks()}
        if path == "/board/tasks/count-by-status":
            return 200, self._reader.count_tasks_by_status()
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["board", "task-publications"]:
            return 200, self._publications.get(
                parts[2],
                self._caller_kid,
                expected_actor_id=params.get("poster_id"),
            )
        if len(parts) == 3 and parts[:2] == ["board", "tasks"]:
            return 200, self._required_row(self._reader.get_task(parts[2]), "task")
        if len(parts) == 3 and parts[:2] == ["board", "bids"]:
            return 200, self._required_row(self._reader.get_bid(parts[2], params["task_id"]), "bid")
        if len(parts) == 3 and parts[:2] == ["board", "assets"]:
            return 200, self._required_row(
                self._reader.get_asset(parts[2], params["task_id"]), "asset"
            )
        if len(parts) == 4 and parts[:2] == ["board", "tasks"]:
            if parts[3] == "input-manifest":
                return 200, read_task_input_manifest(self._writer.connection, parts[2])
            if parts[3] == "bids":
                return 200, {"bids": self._reader.get_bids_for_task(parts[2])}
            if parts[3] == "assets":
                return 200, {"assets": self._reader.get_assets_for_task(parts[2])}
        if len(parts) == 5 and parts[:2] == ["board", "tasks"] and parts[3:] == ["assets", "count"]:
            return 200, {"count": self._reader.count_assets(parts[2])}
        raise AssertionError(f"Unsupported test store read: {path}")

    def _write(self, path: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/board/task-publications":
            result = self._publications.admit(
                parse_task_publication_request(data), self._caller_kid
            )
            return self._publication_status(result), result
        if path == "/board/task-operations":
            result = self._operations.claim_operation(
                parse_task_operation_request(data), self._caller_kid
            )
            return self._operation_status(result), result
        if path == "/board/bids":
            return 201, self._writer.submit_bid(data, validate_constraints(data))
        if path == "/board/assets":
            return 201, self._writer.record_asset(data, validate_constraints(data))
        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[:2] == ["board", "task-publications"]
            and parts[3] == "reconcile"
        ):
            if data != {}:
                raise ServiceError(
                    "invalid_payload", "Reconciliation body must be an empty object", 400, {}
                )
            result = self._publications.reconcile(parts[2], self._caller_kid)
            return self._publication_status(result), result
        if len(parts) == 4 and parts[:2] == ["board", "task-operations"] and parts[3] == "finalize":
            if data != {}:
                raise ServiceError(
                    "invalid_payload", "Finalization body must be an empty object", 400, {}
                )
            result = self._operations.finalize_operation(parts[2], self._caller_kid)
            return self._operation_status(result), result
        if len(parts) == 4 and parts[:2] == ["board", "tasks"] and parts[3] == "status":
            return 200, self._writer.update_task_status(parts[2], data, validate_constraints(data))
        raise AssertionError(f"Unsupported test store write: {path}")

    @staticmethod
    def _required_row(row: dict[str, Any] | None, kind: str) -> dict[str, Any]:
        if row is None:
            raise ServiceError(f"{kind}_not_found", f"{kind.capitalize()} not found", 404, {})
        return row

    @staticmethod
    def _operation_status(result: dict[str, Any]) -> int:
        return 200 if result["operation_state"] == "completed" else 202

    @staticmethod
    def _publication_status(result: dict[str, Any]) -> int:
        state = result["publication_state"]
        if state == "published":
            return 201
        if state == "awaiting_lock":
            return 202
        raise AssertionError(f"Unexpected native publication state: {state}")

    def close(self) -> None:
        """Release this connection while retaining caller-owned persistent evidence."""
        if not self._closed:
            self._writer.close()
            self._closed = True
