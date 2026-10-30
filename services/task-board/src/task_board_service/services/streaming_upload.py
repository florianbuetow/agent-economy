"""Bounded streaming multipart parsing for one authenticated asset file."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field
from python_multipart import MultipartParser
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import parse_options_header
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from python_multipart.multipart import MultipartCallbacks
    from starlette.requests import Request


class AssetUploadLimits(BaseModel):
    """Required positive bounds for one multipart asset request."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_file_size: int = Field(gt=0)
    max_request_size: int = Field(gt=0)
    max_multipart_overhead_bytes: int = Field(gt=0)
    stream_chunk_bytes: int = Field(gt=0)
    max_header_count: int = Field(gt=0)
    max_header_size: int = Field(gt=0)
    max_boundary_bytes: int = Field(gt=0)
    max_display_filename_utf8_bytes: int = Field(gt=0)


class AssetStorageQuota(BaseModel):
    """Required positive totals for stored asset bytes, per uploader and overall."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_total_bytes_per_agent: int = Field(gt=0)
    max_total_bytes: int = Field(gt=0)


@dataclass(frozen=True, slots=True)
class ParsedAssetUpload:
    """Validated metadata derived from the one streamed file part."""

    filename: str
    content_type: str
    size_bytes: int
    content_hash: str


def _invalid_payload(reason: str) -> ServiceError:
    return ServiceError("invalid_payload", "Invalid multipart upload", 400, {"reason": reason})


def _payload_too_large() -> ServiceError:
    return ServiceError("payload_too_large", "Multipart request exceeds its limit", 413, {})


def _file_too_large() -> ServiceError:
    return ServiceError("file_too_large", "File exceeds its declared limit", 413, {})


def _unsafe_filename(filename: str, max_utf8_bytes: int) -> bool:
    try:
        encoded = filename.encode("utf-8")
    except UnicodeEncodeError:
        return True
    if (
        not 1 <= len(encoded) <= max_utf8_bytes
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or (len(filename) >= 2 and filename[0].isalpha() and filename[1] == ":")
    ):
        return True
    return any(
        ord(character) <= 0x1F
        or 0x7F <= ord(character) <= 0x9F
        or 0xD800 <= ord(character) <= 0xDFFF
        or 0x202A <= ord(character) <= 0x202E
        or 0x2066 <= ord(character) <= 0x2069
        for character in filename
    )


def _request_boundary(request: Request, limits: AssetUploadLimits) -> bytes:
    content_type, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if (
        content_type != b"multipart/form-data"
        or boundary is None
        or not boundary
        or len(boundary) > limits.max_boundary_bytes
    ):
        raise _invalid_payload("malformed_multipart")
    return boundary


def _is_complete_upload(
    completed: bool,
    part_count: int,
    file_part_count: int,
    filename: str | None,
    content_type: str | None,
) -> bool:
    return (
        completed
        and part_count == 1
        and file_part_count == 1
        and filename is not None
        and content_type is not None
    )


async def stream_asset_upload(
    request: Request,
    limits: AssetUploadLimits,
    expected_filename: str | None,
    write_chunk: Callable[[bytes], Awaitable[None]],
) -> ParsedAssetUpload:
    """Parse and stream exactly one file part without buffering the request body."""
    boundary = _request_boundary(request, limits)
    pending_writes: list[bytes] = []
    current_header_field = bytearray()
    current_header_value = bytearray()
    current_headers: dict[bytes, bytes] = {}
    part_count = 0
    file_part_count = 0
    file_size = 0
    raw_size = 0
    content_hash = hashlib.sha256()
    filename: str | None = None
    content_type: str | None = None
    completed = False

    def on_part_begin() -> None:
        nonlocal part_count
        part_count += 1
        current_headers.clear()
        current_header_field.clear()
        current_header_value.clear()

    def on_header_field(data: bytes, start: int, end: int) -> None:
        current_header_field.extend(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        current_header_value.extend(data[start:end])

    def on_header_end() -> None:
        name = bytes(current_header_field).lower()
        if not name or name in current_headers:
            raise _invalid_payload("malformed_multipart")
        current_headers[name] = bytes(current_header_value)
        current_header_field.clear()
        current_header_value.clear()

    def on_headers_finished() -> None:
        nonlocal content_type, file_part_count, filename
        disposition = current_headers.get(b"content-disposition")
        if disposition is None:
            raise _invalid_payload("malformed_multipart")
        disposition_type, options = parse_options_header(disposition)
        raw_filename = options.get(b"filename")
        if (
            disposition_type != b"form-data"
            or options.get(b"name") != b"file"
            or raw_filename is None
            or part_count != 1
        ):
            raise _invalid_payload("malformed_multipart")
        try:
            parsed_filename = raw_filename.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _invalid_payload("invalid_filename") from exc
        if _unsafe_filename(parsed_filename, limits.max_display_filename_utf8_bytes):
            raise _invalid_payload("invalid_filename")
        if expected_filename is not None and parsed_filename != expected_filename:
            raise _invalid_payload("filename_mismatch")
        raw_content_type = current_headers.get(b"content-type")
        if raw_content_type is None:
            raise _invalid_payload("malformed_multipart")
        try:
            parsed_content_type = raw_content_type.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise _invalid_payload("malformed_multipart") from exc
        file_part_count += 1
        filename = parsed_filename
        content_type = parsed_content_type

    def on_part_data(data: bytes, start: int, end: int) -> None:
        nonlocal file_size
        if file_part_count != 1:
            raise _invalid_payload("malformed_multipart")
        value = bytes(data[start:end])
        new_size = file_size + len(value)
        if new_size > limits.max_file_size:
            raise _file_too_large()
        file_size = new_size
        content_hash.update(value)
        for offset in range(0, len(value), limits.stream_chunk_bytes):
            pending_writes.append(value[offset : offset + limits.stream_chunk_bytes])

    def on_part_end() -> None:
        if file_part_count != 1 or part_count != 1:
            raise _invalid_payload("malformed_multipart")

    def on_end() -> None:
        nonlocal completed
        completed = True

    callbacks: MultipartCallbacks = {
        "on_part_begin": on_part_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
        "on_end": on_end,
    }
    try:
        parser = MultipartParser(
            boundary,
            callbacks=callbacks,
            max_header_count=limits.max_header_count,
            max_header_size=limits.max_header_size,
        )
        async for request_chunk in request.stream():
            for offset in range(0, len(request_chunk), limits.stream_chunk_bytes):
                parser_chunk = request_chunk[offset : offset + limits.stream_chunk_bytes]
                raw_size += len(parser_chunk)
                parser.write(parser_chunk)
                writes = tuple(pending_writes)
                pending_writes.clear()
                for chunk in writes:
                    await write_chunk(chunk)
                if raw_size > limits.max_request_size:
                    raise _payload_too_large()
        parser.finalize()
    except ServiceError:
        raise
    except FormParserError as exc:
        raise _invalid_payload("malformed_multipart") from exc

    if not _is_complete_upload(
        completed,
        part_count,
        file_part_count,
        filename,
        content_type,
    ):
        raise _invalid_payload("malformed_multipart")
    assert filename is not None
    assert content_type is not None
    if raw_size - file_size > limits.max_multipart_overhead_bytes:
        raise _payload_too_large()
    return ParsedAssetUpload(
        filename=filename,
        content_type=content_type,
        size_bytes=file_size,
        content_hash=content_hash.hexdigest(),
    )


__all__ = [
    "AssetStorageQuota",
    "AssetUploadLimits",
    "ParsedAssetUpload",
    "stream_asset_upload",
]
