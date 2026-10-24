"""Regression proofs for bounded parsing of one large ASGI request chunk."""

from __future__ import annotations

import asyncio
import hashlib
import tracemalloc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError
from starlette.requests import Request

from task_board_service.services import streaming_upload as streaming_upload_module
from task_board_service.services.streaming_upload import (
    AssetUploadLimits,
    stream_asset_upload,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_STREAM_CHUNK_BYTES = 65_536
_PARSER_PEAK_LIMIT_BYTES = _STREAM_CHUNK_BYTES * 16


class _OneChunkReceive:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.calls = 0

    async def __call__(self) -> dict[str, object]:
        self.calls += 1
        if self.calls == 1:
            return {"type": "http.request", "body": self._body, "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}


@dataclass
class _HashProbe:
    hashed_bytes: int = 0


class _ObservedHash:
    def __init__(self, factory: Callable[[], Any], probe: _HashProbe) -> None:
        self._inner = factory()
        self._probe = probe

    def update(self, value: bytes) -> None:
        self._probe.hashed_bytes += len(value)
        self._inner.update(value)

    def hexdigest(self) -> str:
        return str(self._inner.hexdigest())


class _NonRetainingSink:
    def __init__(self, probe: _HashProbe) -> None:
        self._probe = probe
        self.total_bytes = 0
        self.digest = hashlib.new("sha256")
        self.max_chunk_bytes = 0
        self.calls = 0
        self.hashed_bytes_at_first_await: int | None = None
        self.peak_bytes_at_first_await: int | None = None

    async def write(self, chunk: bytes) -> None:
        self.calls += 1
        if self.calls == 1:
            self.hashed_bytes_at_first_await = self._probe.hashed_bytes
            _current, peak = tracemalloc.get_traced_memory()
            self.peak_bytes_at_first_await = peak
        self.total_bytes += len(chunk)
        self.digest.update(chunk)
        self.max_chunk_bytes = max(self.max_chunk_bytes, len(chunk))
        await asyncio.sleep(0)


def _multipart_body(boundary: str, content: bytes) -> tuple[bytes, int]:
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="evidence.txt"\r\n'
        "Content-Type: text/plain\r\n"
        "\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    return b"".join((prefix, content, suffix)), len(prefix) + len(suffix)


def _request(boundary: str, body: bytes) -> tuple[Request, _OneChunkReceive]:
    receive = _OneChunkReceive(body)
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/tasks/t-large-chunk/assets",
        "raw_path": b"/tasks/t-large-chunk/assets",
        "query_string": b"",
        "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    return Request(scope, receive=receive), receive


def _limits(*, max_file_size: int, max_request_size: int, overhead: int) -> AssetUploadLimits:
    return AssetUploadLimits(
        max_file_size=max_file_size,
        max_request_size=max_request_size,
        max_multipart_overhead_bytes=overhead,
        stream_chunk_bytes=_STREAM_CHUNK_BYTES,
        max_header_count=4,
        max_header_size=512,
        max_boundary_bytes=32,
        max_display_filename_utf8_bytes=64,
    )


def _install_hash_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> _HashProbe:
    probe = _HashProbe()
    original_sha256 = hashlib.sha256
    monkeypatch.setattr(
        streaming_upload_module.hashlib,
        "sha256",
        lambda: _ObservedHash(original_sha256, probe),
    )
    return probe


@pytest.mark.unit
async def test_single_large_transport_chunk_flushes_before_complete_file_is_processed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parser-owned memory and hash progress remain bounded before first sink await."""
    boundary = "large-success"
    content = b"a" * (2 * 1_048_576)
    expected_digest = hashlib.sha256(content).hexdigest()
    body, overhead = _multipart_body(boundary, content)
    request, receive = _request(boundary, body)
    limits = _limits(
        max_file_size=len(content),
        max_request_size=len(body),
        overhead=overhead,
    )
    probe = _install_hash_probe(monkeypatch)
    sink = _NonRetainingSink(probe)

    tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        result = await stream_asset_upload(
            request,
            limits,
            "evidence.txt",
            sink.write,
        )
        _current, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    diagnostics = {
        "transport_body_bytes_excluded_from_trace": len(body),
        "receive_calls": receive.calls,
        "sink_calls": sink.calls,
        "hashed_bytes_at_first_await": sink.hashed_bytes_at_first_await,
        "peak_bytes_at_first_await": sink.peak_bytes_at_first_await,
        "overall_parser_peak_bytes": peak_bytes,
        "parser_peak_limit_bytes": _PARSER_PEAK_LIMIT_BYTES,
    }
    assert sink.hashed_bytes_at_first_await is not None, diagnostics
    assert sink.hashed_bytes_at_first_await <= _STREAM_CHUNK_BYTES, diagnostics
    assert sink.peak_bytes_at_first_await is not None, diagnostics
    assert sink.peak_bytes_at_first_await <= _PARSER_PEAK_LIMIT_BYTES, diagnostics
    assert peak_bytes <= _PARSER_PEAK_LIMIT_BYTES, diagnostics
    assert sink.max_chunk_bytes <= _STREAM_CHUNK_BYTES, diagnostics
    assert sink.total_bytes == len(content), diagnostics
    assert sink.digest.hexdigest() == expected_digest, diagnostics
    assert result.size_bytes == len(content), diagnostics
    assert result.content_hash == expected_digest, diagnostics


@pytest.mark.unit
async def test_single_large_transport_chunk_file_overflow_has_bounded_parser_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overflow may receive one huge transport chunk but cannot copy it wholesale."""
    boundary = "large-overflow"
    file_limit = 1_048_576
    content = b"z" * (4 * 1_048_576)
    body, overhead = _multipart_body(boundary, content)
    request, receive = _request(boundary, body)
    limits = _limits(
        max_file_size=file_limit,
        max_request_size=len(body),
        overhead=overhead,
    )
    probe = _install_hash_probe(monkeypatch)
    sink = _NonRetainingSink(probe)
    error: ServiceError | None = None

    tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        try:
            await stream_asset_upload(
                request,
                limits,
                "evidence.txt",
                sink.write,
            )
        except ServiceError as caught:
            error = caught
        _current, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    diagnostics = {
        "transport_body_bytes_excluded_from_trace": len(body),
        "receive_calls": receive.calls,
        "sink_calls": sink.calls,
        "hashed_bytes": probe.hashed_bytes,
        "overall_parser_peak_bytes": peak_bytes,
        "parser_peak_limit_bytes": _PARSER_PEAK_LIMIT_BYTES,
    }
    assert error is not None, diagnostics
    assert (error.status_code, error.error) == (413, "file_too_large"), diagnostics
    assert receive.calls == 1, diagnostics
    assert peak_bytes <= _PARSER_PEAK_LIMIT_BYTES, diagnostics
    assert probe.hashed_bytes <= file_limit + _STREAM_CHUNK_BYTES, diagnostics
