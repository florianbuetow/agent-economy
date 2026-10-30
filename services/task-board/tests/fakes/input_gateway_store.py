"""Test-only Gateway store adding the V2 poster-input and V6 artifact routes.

Every added path dispatches to the production Gateway writer or runtime function
for that route, on the same real SQLite database, with explicit caller authority.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from db_gateway_service.artifact_upload_requests import parse_artifact_policy
from db_gateway_service.services import artifact_runtime
from db_gateway_service.services.prepublication_writer import PrepublicationWriter
from db_gateway_service.task_publication_v2_requests import (
    parse_input_manifest_freeze_v2,
    parse_publication_admission_v2,
    parse_publication_publish_v2,
)

from tests.fakes.gateway_task_store import GatewayTaskStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from db_gateway_service.board_authority import BoardAuthority


class InputGatewayStore(GatewayTaskStore):
    """GatewayTaskStore plus /board/prepublications/* and /board/artifact-uploads/*."""

    def __init__(
        self,
        *,
        db_path: Path,
        caller_kid: str,
        authority: BoardAuthority,
        clock_us: Callable[[], int],
    ) -> None:
        super().__init__(
            db_path=db_path,
            busy_timeout_ms=5000,
            journal_mode="wal",
            schema_sql=None,
            caller_kid=caller_kid,
            authority=authority,
            clock_us=clock_us,
        )
        self._kid = caller_kid
        self._clock = clock_us

    def _prepublications(self, policy: dict[str, Any] | None) -> PrepublicationWriter:
        return PrepublicationWriter(
            self.writer.connection,
            clock_us=self._clock,
            artifact_policy=None if policy is None else parse_artifact_policy(policy),
            current_task_board_caller_kid=self._kid,
        )

    def _read(self, path: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        parts = path.strip("/").split("/")
        connection = self.writer.connection
        if len(parts) == 3 and parts[:2] == ["board", "prepublications"]:
            envelope = self._prepublications(None).get(
                parts[2], verified_caller_kid=self._kid, expected_actor_id=params["poster_id"]
            )
            stored = connection.execute(
                "SELECT canonical_request_json FROM board_task_publications WHERE operation_id=?",
                (parts[2],),
            ).fetchone()
            return 200, {**envelope, "reward": json.loads(str(stored[0]))["reward"]}
        if len(parts) == 4 and parts[:2] == ["board", "tasks"]:
            if parts[3] == "artifact-context":
                return 200, artifact_runtime.task_artifact_context(connection, parts[2])
            if parts[3] == "artifacts":
                return 200, artifact_runtime.list_task_artifacts(connection, parts[2])
        if len(parts) == 3 and parts[:2] == ["board", "artifact-uploads"]:
            return 200, artifact_runtime.get_artifact_upload(
                connection, parts[2], caller_kid=self._kid
            )
        return super()._read(path, params)

    def _write(self, path: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        parts = path.strip("/").split("/")
        connection = self.writer.connection
        if parts == ["board", "prepublications"]:
            return 202, self._prepublications(data["policy"]).admit(
                parse_publication_admission_v2(data["request"]), verified_caller_kid=self._kid
            )
        if len(parts) >= 4 and parts[:2] == ["board", "prepublications"]:
            writer = self._prepublications(None)
            if parts[3:] == ["inputs", "freeze"]:
                return 200, writer.freeze_inputs(
                    parse_input_manifest_freeze_v2(data), verified_caller_kid=self._kid
                )
            if parts[3:] == ["publish"]:
                return 202, writer.authorize_publish(
                    parse_publication_publish_v2(data), verified_caller_kid=self._kid
                )
            if parts[3:] == ["reconcile"]:
                result = writer.reconcile(parts[2], verified_caller_kid=self._kid)
                return (201 if result["publication_state"] == "published" else 202), result
        if parts == ["board", "artifact-uploads", "reserve"]:
            return 200, artifact_runtime.reserve_artifact_upload(
                connection,
                data,
                caller_kid=self._kid,
                clock_us=self._clock,
                current_task_board_caller_kid=self._kid,
            )
        if len(parts) == 4 and parts[:2] == ["board", "artifact-uploads"]:
            operation = {
                "finalize": artifact_runtime.finalize_artifact_upload,
                "orphan": artifact_runtime.orphan_artifact_upload,
                "renew": artifact_runtime.renew_artifact_upload,
            }[parts[3]]
            return 200, operation(
                connection,
                parts[2],
                data,
                caller_kid=self._kid,
                clock_us=self._clock,
                current_task_board_caller_kid=self._kid,
            )
        return super()._write(path, data)


__all__ = ["InputGatewayStore"]
