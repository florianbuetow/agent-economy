"""Runtime dependency resolution for streamed asset uploads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_board_service.config import get_settings
from task_board_service.services.streaming_upload import AssetUploadLimits

if TYPE_CHECKING:
    from task_board_service.services.asset_manager import AssetManager


def resolve_asset_upload_limits(asset_manager: AssetManager) -> AssetUploadLimits:
    """Build explicit upload limits at the core configuration boundary."""
    settings = get_settings()
    upload = settings.asset_upload
    return AssetUploadLimits(
        max_file_size=asset_manager.max_file_size,
        max_request_size=settings.request.max_body_size,
        max_multipart_overhead_bytes=upload.max_multipart_overhead_bytes,
        stream_chunk_bytes=upload.stream_chunk_bytes,
        max_header_count=upload.max_header_count,
        max_header_size=upload.max_header_size,
        max_boundary_bytes=upload.max_boundary_bytes,
        max_display_filename_utf8_bytes=upload.max_display_filename_utf8_bytes,
    )


__all__ = ["resolve_asset_upload_limits"]
