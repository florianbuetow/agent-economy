"""Asset upload, listing, and download endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from service_commons.exceptions import ServiceError
from starlette.datastructures import UploadFile as StarletteUploadFile

from task_board_service.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: Bearer token extraction from Authorization header
# ---------------------------------------------------------------------------


def _extract_bearer_token(authorization: str | None) -> str:
    """Extract JWS token from Authorization header.

    Unlike the bids router version, this always requires the token.
    Raises INVALID_JWS if the header is missing, malformed, or empty.
    """
    if authorization is None:
        raise ServiceError(
            "INVALID_JWS",
            "Missing Authorization header",
            400,
            {},
        )

    if not authorization.startswith("Bearer "):
        raise ServiceError(
            "INVALID_JWS",
            "Authorization header must use Bearer scheme",
            400,
            {},
        )

    token = authorization[len("Bearer ") :]
    if not token:
        raise ServiceError(
            "INVALID_JWS",
            "Bearer token must not be empty",
            400,
            {},
        )

    return token


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/assets — upload asset
# MUST be before GET /tasks/{task_id}/assets/{asset_id}
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/assets", status_code=201)
async def upload_asset(task_id: str, request: Request) -> JSONResponse:
    """Upload a deliverable asset (multipart/form-data)."""
    # Extract auth token from Authorization header
    authorization = request.headers.get("authorization")
    token = _extract_bearer_token(authorization)

    # Parse multipart form data
    form = await request.form()
    upload_file = form.get("file")

    if upload_file is None:
        raise ServiceError(
            "NO_FILE",
            "No file part in the multipart request",
            400,
            {},
        )
    if not isinstance(upload_file, StarletteUploadFile):
        raise ServiceError(
            "NO_FILE",
            "File field must be an uploaded file",
            400,
            {},
        )
    parsed_upload_file = upload_file

    # Read file content and metadata
    content = await parsed_upload_file.read()
    filename = parsed_upload_file.filename or "unnamed"
    content_type = parsed_upload_file.content_type or "application/octet-stream"

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.upload_asset(
        task_id,
        token,
        content,
        filename,
        content_type,
    )
    return JSONResponse(status_code=201, content=result)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/assets — list assets (public)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/assets")
async def list_assets(task_id: str) -> dict[str, Any]:
    """List all assets for a task."""
    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    return await state.task_manager.list_assets(task_id)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/assets/{asset_id} — download asset (public)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/assets/{asset_id}")
async def download_asset(task_id: str, asset_id: str) -> Response:
    """Download an asset file."""
    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    content, content_type, filename = await state.task_manager.download_asset(
        task_id,
        asset_id,
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
