"""Focused regressions for unsigned multipart display filenames."""

from __future__ import annotations

from typing import Any

import pytest
from service_commons.exceptions import ServiceError
from starlette.requests import Request

from task_board_service.services.streaming_upload import AssetUploadLimits, stream_asset_upload


def _request(filename: str) -> Request:
    boundary = "filename-policy"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/plain\r\n"
        "\r\n"
        "exact bytes\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/tasks/t-filename/assets",
        "raw_path": b"/tasks/t-filename/assets",
        "query_string": b"",
        "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    return Request(scope, receive=receive)


def _limits() -> AssetUploadLimits:
    return AssetUploadLimits(
        max_file_size=64,
        max_request_size=512,
        max_multipart_overhead_bytes=256,
        stream_chunk_bytes=8,
        max_header_count=4,
        max_header_size=256,
        max_boundary_bytes=32,
        max_display_filename_utf8_bytes=64,
    )


@pytest.mark.unit
async def test_unsigned_filename_is_accepted_but_explicit_mismatch_is_rejected() -> None:
    """None accepts safe display metadata while an explicit expected value stays exact."""
    written: list[bytes] = []

    async def write_chunk(chunk: bytes) -> None:
        written.append(chunk)

    parsed = await stream_asset_upload(
        _request("actual.txt"),
        _limits(),
        None,
        write_chunk,
    )
    assert parsed.filename == "actual.txt"
    assert b"".join(written) == b"exact bytes"

    with pytest.raises(ServiceError) as raised:
        await stream_asset_upload(
            _request("actual.txt"),
            _limits(),
            "expected.txt",
            write_chunk,
        )
    assert (
        raised.value.status_code,
        raised.value.error,
        raised.value.details.get("reason"),
    ) == (400, "invalid_payload", "filename_mismatch")
