"""Protocol-V2 poster-input publication endpoints (completion plan 2.4, G03).

Admit -> (input uploads through the artifact reservation routes) -> freeze the
input manifest -> authorize publish -> Bank escrow lock -> reconcile.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from db_gateway_service import task_time
from db_gateway_service.artifact_upload_requests import ArtifactPolicy, parse_artifact_policy
from db_gateway_service.config import get_settings
from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import parse_json_body
from db_gateway_service.services.input_manifest_reader import read_task_input_manifest
from db_gateway_service.services.prepublication_writer import PrepublicationWriter
from db_gateway_service.task_operation_requests import parse_operation_id
from db_gateway_service.task_publication_v2_requests import (
    parse_input_manifest_freeze_v2,
    parse_publication_admission_v2,
    parse_publication_publish_v2,
)

router = APIRouter(prefix="/board", tags=["Board"])


def _context(
    request: Request,
    policy: ArtifactPolicy | None,
) -> tuple[PrepublicationWriter, str]:
    """Bind the verified caller and the Task Board-supplied policy to one writer."""
    caller_kid: object = getattr(request.state, "gateway_caller_kid", None)
    if not isinstance(caller_kid, str) or not caller_kid:
        raise ServiceError("forbidden", "Authenticated Gateway caller is required", 403, {})
    writer = get_app_state().db_writer
    if writer is None:
        raise ServiceError("service_not_ready", "Publication authority is not ready", 503, {})
    return (
        PrepublicationWriter(
            writer.connection,
            clock_us=task_time.utc_now_us,
            artifact_policy=policy,
            current_task_board_caller_kid=(
                get_settings().board_effect_recovery.current_task_board_caller_kid
            ),
        ),
        caller_kid,
    )


def _path_publication(operation_id: str, body_publication_id: str) -> None:
    if parse_operation_id(operation_id) != body_publication_id:
        raise ServiceError(
            "invalid_payload",
            "publication_operation_id does not match the URL path",
            400,
            {},
        )


@router.post("/prepublications")
async def admit_prepublication(request: Request) -> JSONResponse:
    """Reserve a V2 posting identity and its draft input manifest before escrow."""
    body: object = parse_json_body(await request.body())
    if not isinstance(body, dict) or set(body) != {"request", "policy"}:
        raise ServiceError(
            "invalid_payload", "Body must contain exactly request and policy", 400, {}
        )
    policy = parse_artifact_policy(body["policy"])
    command = parse_publication_admission_v2(body["request"])
    writer, caller_kid = _context(request, policy)
    return JSONResponse(
        status_code=202, content=writer.admit(command, verified_caller_kid=caller_kid)
    )


@router.post("/prepublications/{operation_id}/inputs/freeze")
async def freeze_prepublication_inputs(operation_id: str, request: Request) -> JSONResponse:
    """Freeze the exact ready input set (names, sizes, sha256) into the manifest."""
    command = parse_input_manifest_freeze_v2(parse_json_body(await request.body()))
    _path_publication(operation_id, command.publication_operation_id)
    writer, caller_kid = _context(request, None)
    return JSONResponse(
        status_code=200, content=writer.freeze_inputs(command, verified_caller_kid=caller_kid)
    )


@router.post("/prepublications/{operation_id}/publish")
async def authorize_prepublication_publish(operation_id: str, request: Request) -> JSONResponse:
    """Authorize the escrow lock for the frozen publication contract."""
    command = parse_publication_publish_v2(parse_json_body(await request.body()))
    _path_publication(operation_id, command.publication_operation_id)
    writer, caller_kid = _context(request, None)
    return JSONResponse(
        status_code=202,
        content=writer.authorize_publish(command, verified_caller_kid=caller_kid),
    )


@router.post("/prepublications/{operation_id}/reconcile")
async def reconcile_prepublication(operation_id: str, request: Request) -> JSONResponse:
    """Publish from persisted Bank lock evidence only."""
    if parse_json_body(await request.body()) != {}:
        raise ServiceError(
            "invalid_payload", "Reconciliation body must be an empty object", 400, {}
        )
    writer, caller_kid = _context(request, None)
    result = writer.reconcile(operation_id, verified_caller_kid=caller_kid)
    status = 201 if result["publication_state"] == "published" else 202
    return JSONResponse(status_code=status, content=result)


@router.get("/prepublications-recovery")
async def list_prepublication_recovery(request: Request) -> JSONResponse:
    """List V2 publications authorized but not yet reconciled with their lock."""
    query = request.query_params
    if set(query) != {"after", "limit"} or any(
        len(query.getlist(name)) != 1 for name in ("after", "limit")
    ):
        raise ServiceError("invalid_payload", "Exactly one after and limit are required", 400, {})
    limit_text = query["limit"]
    if re.fullmatch(r"[1-9][0-9]{0,3}", limit_text) is None:
        raise ServiceError("invalid_payload", "limit must be an integer in 1..1000", 400, {})
    writer, caller_kid = _context(request, None)
    return JSONResponse(
        status_code=200,
        content=writer.recovery(query["after"], int(limit_text), verified_caller_kid=caller_kid),
    )


@router.get("/prepublications/{operation_id}")
async def get_prepublication(operation_id: str, request: Request) -> JSONResponse:
    """Read one V2 publication for its owning poster."""
    query = request.query_params
    if set(query) != {"poster_id"} or len(query.getlist("poster_id")) != 1:
        raise ServiceError("invalid_payload", "Exactly one poster_id is required", 400, {})
    poster_id = query["poster_id"]
    if poster_id == "":
        raise ServiceError("invalid_payload", "poster_id must be a nonempty string", 400, {})
    writer, caller_kid = _context(request, None)
    envelope = writer.get(operation_id, verified_caller_kid=caller_kid, expected_actor_id=poster_id)
    db_writer = get_app_state().db_writer
    assert db_writer is not None
    stored = db_writer.connection.execute(
        "SELECT canonical_request_json FROM board_task_publications WHERE operation_id=?",
        (envelope["publication_operation_id"],),
    ).fetchone()
    # The writer has already validated the stored admission; expose its reward so
    # Task Board can bind the poster's escrow token to it before any lock.
    admission = parse_publication_admission_v2(json.loads(str(stored[0])))
    return JSONResponse(status_code=200, content={**envelope, "reward": admission.reward})


@router.get("/tasks/{task_id}/input-manifest")
async def get_task_input_manifest(task_id: str) -> JSONResponse:
    """Public read of a task's frozen input manifest (``null`` for V1 tasks)."""
    writer = get_app_state().db_writer
    if writer is None:
        raise ServiceError("service_not_ready", "Database writer is not ready", 503, {})
    return JSONResponse(
        status_code=200, content=read_task_input_manifest(writer.connection, task_id)
    )
