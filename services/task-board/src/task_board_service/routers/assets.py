"""Asset upload, listing, and download endpoints."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from service_commons.exceptions import ServiceError

from task_board_service.core.asset_upload import resolve_asset_upload_limits
from task_board_service.core.state import get_app_state
from task_board_service.routers.validation import extract_bearer_token
from task_board_service.schemas import AssetListResponse, AssetResponse

router = APIRouter()

_RFC8187_ATTR_CHARACTERS = "!#$&+-.^_`|~"


def _content_disposition(filename: str, asset_id: str) -> str:
    """Build RFC 6266 filename parameters with an exact RFC 8187 UTF-8 value."""
    printable_ascii = filename.isascii() and all(0x20 <= ord(char) < 0x7F for char in filename)
    fallback = filename if printable_ascii else asset_id
    quoted_fallback = fallback.replace("\\", "\\\\").replace('"', '\\"')
    extended = quote(filename, safe=_RFC8187_ATTR_CHARACTERS, encoding="utf-8", errors="strict")
    return f"attachment; filename=\"{quoted_fallback}\"; filename*=UTF-8''{extended}"


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/assets — upload asset
# MUST be before GET /tasks/{task_id}/assets/{asset_id}
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/assets", status_code=201, response_model=AssetResponse)
async def upload_asset(task_id: str, request: Request) -> dict[str, Any]:
    """Upload a deliverable asset (multipart/form-data)."""
    authorization = request.headers.get("authorization")
    token = extract_bearer_token(authorization, required=True)
    if token is None:
        raise ServiceError(
            error="unauthorized",
            message="Authorization token must be present",
            status_code=401,
            details={},
        )

    state = get_app_state()
    if state.asset_manager is None:
        raise ServiceError(
            error="service_not_ready",
            message="AssetManager not initialized",
            status_code=503,
            details={},
        )

    return await state.asset_manager.upload_streaming_asset(
        task_id=task_id,
        token=token,
        request=request,
        limits=resolve_asset_upload_limits(state.asset_manager),
    )


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/assets — list assets (public)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/assets", response_model=AssetListResponse)
async def list_assets(task_id: str) -> dict[str, Any]:
    """List all assets for a task."""
    state = get_app_state()
    if state.asset_manager is None:
        raise ServiceError(
            error="service_not_ready",
            message="AssetManager not initialized",
            status_code=503,
            details={},
        )

    return await state.asset_manager.list_assets(task_id)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/assets/{asset_id} — download asset (public)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/assets/{asset_id}")
async def download_asset(task_id: str, asset_id: str) -> Response:
    """Download an asset file."""
    state = get_app_state()
    if state.asset_manager is None:
        raise ServiceError(
            error="service_not_ready",
            message="AssetManager not initialized",
            status_code=503,
            details={},
        )

    content, content_type, filename = await state.asset_manager.download_asset(
        task_id,
        asset_id,
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": _content_disposition(filename, asset_id),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
