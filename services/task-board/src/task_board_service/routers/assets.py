"""Asset upload, listing, and download endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from service_commons.exceptions import ServiceError

from task_board_service.core.asset_upload import (
    resolve_asset_storage_quota,
    resolve_asset_upload_limits,
)
from task_board_service.core.state import get_app_state
from task_board_service.routers.validation import extract_bearer_token
from task_board_service.schemas import IDEMPOTENCY_KEY_HEADER, AssetListResponse, AssetResponse

if TYPE_CHECKING:
    from task_board_service.services.asset_manager import AssetManager

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

    asset_manager = _require_asset_manager()
    return await asset_manager.upload_streaming_asset(
        task_id=task_id,
        token=token,
        request=request,
        limits=resolve_asset_upload_limits(asset_manager),
        quota=resolve_asset_storage_quota(),
        idempotency_key=request.headers.get(IDEMPOTENCY_KEY_HEADER),
    )


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/assets — list assets (signed task principals only)
# ---------------------------------------------------------------------------


def _read_token(request: Request) -> str:
    token = extract_bearer_token(request.headers.get("authorization"), required=False)
    if token is None:
        raise ServiceError(
            error="unauthorized",
            message="Authorization token must be present",
            status_code=401,
            details={},
        )
    return token


def _require_asset_manager() -> AssetManager:
    state = get_app_state()
    if state.asset_manager is None:
        raise ServiceError(
            error="service_not_ready",
            message="AssetManager not initialized",
            status_code=503,
            details={},
        )
    return state.asset_manager


@router.get("/tasks/{task_id}/manifests")
async def list_manifests(task_id: str, request: Request) -> dict[str, Any]:
    """Return the task's input/submission manifests (canonical JSON plus SHA-256)."""
    token = _read_token(request)
    return await _require_asset_manager().list_manifests_for_reader(task_id, token)


@router.get("/tasks/{task_id}/assets", response_model=AssetListResponse)
async def list_assets(task_id: str, request: Request) -> dict[str, Any]:
    """List all assets for a task (poster, worker, platform or operator)."""
    token = _read_token(request)
    return await _require_asset_manager().list_assets_for_reader(task_id, token)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/assets/{asset_id} — download asset (signed task principals only)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/assets/{asset_id}")
async def download_asset(task_id: str, asset_id: str, request: Request) -> Response:
    """Download an asset file as an opaque attachment."""
    token = _read_token(request)
    content, _content_type, filename = await _require_asset_manager().download_asset_for_reader(
        task_id,
        asset_id,
        token,
    )
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": _content_disposition(filename, asset_id),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
