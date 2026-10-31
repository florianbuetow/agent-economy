"""Poster input attachment routes (completion plan 2.4, G03).

POST /task-publications                      admit a signed posting intent (202)
POST /task-publications/{id}/inputs          upload one input file (201)
POST /task-publications/{id}/publish         freeze inputs, lock escrow, publish (201/202)
GET  /tasks/{task_id}/inputs                 public frozen input manifest (200)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Request, Response
from service_commons.exceptions import ServiceError

from task_board_service.core.asset_upload import (
    resolve_asset_storage_quota,
    resolve_asset_upload_limits,
)
from task_board_service.core.state import get_app_state
from task_board_service.routers.validation import (
    extract_bearer_token,
    extract_token,
    parse_json_body,
)
from task_board_service.schemas import IDEMPOTENCY_KEY_HEADER
from task_board_service.services.input_manager import (
    PosterInputCoordinator,
    public_input_manifest,
)

if TYPE_CHECKING:
    from task_board_service.services.asset_manager import AssetManager
    from task_board_service.services.input_manager import (
        AgentTokenValidator,
        EscrowLocker,
        InputUploader,
        PrepublicationStore,
    )

router = APIRouter()


def _not_ready(component: str) -> ServiceError:
    return ServiceError("service_not_ready", f"{component} not initialized", 503, {})


def _asset_manager() -> AssetManager:
    manager = get_app_state().asset_manager
    if manager is None:
        raise _not_ready("AssetManager")
    return manager


def _coordinator() -> PosterInputCoordinator:
    state = get_app_state()
    if state.store is None:
        raise _not_ready("Task store")
    if state.token_validator is None:
        raise _not_ready("TokenValidator")
    if state.central_bank_client is None:
        raise _not_ready("Central Bank client")
    return PosterInputCoordinator(
        store=cast("PrepublicationStore", state.store),
        token_validator=cast("AgentTokenValidator", state.token_validator),
        bank=cast("EscrowLocker", state.central_bank_client),
        uploader=cast("InputUploader", _asset_manager()),
    )


@router.post("/task-publications", status_code=202)
async def admit_task_publication(request: Request) -> dict[str, Any]:
    """Admit a poster-signed posting intent before escrow; returns the input manifest id."""
    data = parse_json_body(await request.body())
    return await _coordinator().admit(extract_token(data, "token"))


@router.post("/task-publications/{publication_operation_id}/inputs", status_code=201)
async def upload_task_input(publication_operation_id: str, request: Request) -> dict[str, Any]:
    """Upload one input file (multipart/form-data) into the poster's draft manifest."""
    token = extract_bearer_token(request.headers.get("authorization"), required=True)
    if token is None:
        raise ServiceError("unauthorized", "Authorization token must be present", 401, {})
    manager = _asset_manager()
    return await _coordinator().upload_input(
        publication_operation_id,
        token=token,
        request=request,
        limits=resolve_asset_upload_limits(manager),
        quota=resolve_asset_storage_quota(),
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
    )


@router.post("/task-publications/{publication_operation_id}/publish", status_code=201)
async def publish_task_publication(
    publication_operation_id: str, request: Request, response: Response
) -> dict[str, Any]:
    """Freeze the input manifest and publish the task bound to it (locks escrow)."""
    data = parse_json_body(await request.body())
    status, body = await _coordinator().publish(
        publication_operation_id,
        task_token=extract_token(data, "task_token"),
        escrow_token=extract_token(data, "escrow_token"),
    )
    response.status_code = status
    return body


@router.get("/tasks/{task_id}/inputs")
async def get_task_inputs(task_id: str) -> dict[str, Any]:
    """Public frozen input manifest: names, sizes, sha256 and downloadable asset ids."""
    store = get_app_state().store
    if store is None:
        raise _not_ready("Task store")
    manifest = cast("PrepublicationStore", store).get_task_input_manifest(task_id)
    raw = manifest["input_manifest"]
    return {
        "task_id": task_id,
        "input_manifest": None if raw is None else public_input_manifest(raw),
    }
