"""T-126 contract for the isolated configurable streaming parser component."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from pydantic import ValidationError
from service_commons.exceptions import ServiceError
from starlette.requests import Request

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


_DEFAULT_LIMIT_VALUES = {
    "max_file_size": 64,
    "max_request_size": 2_048,
    "max_multipart_overhead_bytes": 1_024,
    "stream_chunk_bytes": 4,
    "max_header_count": 4,
    "max_header_size": 256,
    "max_boundary_bytes": 8,
    "max_display_filename_utf8_bytes": 32,
}


class _Limits(Protocol):
    max_file_size: int
    max_request_size: int
    max_multipart_overhead_bytes: int
    stream_chunk_bytes: int
    max_header_count: int
    max_header_size: int
    max_boundary_bytes: int
    max_display_filename_utf8_bytes: int


class _ParsedUpload(Protocol):
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str


class _LimitsType(Protocol):
    def __call__(
        self,
        *,
        max_file_size: int,
        max_request_size: int,
        max_multipart_overhead_bytes: int,
        stream_chunk_bytes: int,
        max_header_count: int,
        max_header_size: int,
        max_boundary_bytes: int,
        max_display_filename_utf8_bytes: int,
    ) -> _Limits: ...


class _StreamUpload(Protocol):
    def __call__(
        self,
        request: Request,
        limits: _Limits,
        expected_filename: str,
        write_chunk: Callable[[bytes], Awaitable[None]],
    ) -> Awaitable[_ParsedUpload]: ...


@dataclass(frozen=True)
class _Api:
    limits_type: _LimitsType
    stream_upload: _StreamUpload


class _Receive:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded_chunks = 0
        self.yielded_bytes = 0

    async def __call__(self) -> dict[str, object]:
        if self.yielded_chunks >= len(self.chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk = self.chunks[self.yielded_chunks]
        self.yielded_chunks += 1
        self.yielded_bytes += len(chunk)
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": self.yielded_chunks < len(self.chunks),
        }


class _Sink:
    def __init__(self, receive: _Receive) -> None:
        self._receive = receive
        self.chunks: list[bytes] = []
        self.receive_chunks_at_write: list[int] = []
        self.receive_bytes_at_write: list[int] = []

    async def write(self, chunk: bytes) -> None:
        self.chunks.append(bytes(chunk))
        self.receive_chunks_at_write.append(self._receive.yielded_chunks)
        self.receive_bytes_at_write.append(self._receive.yielded_bytes)


def _api() -> _Api:
    module_name = "task_board_service.services.streaming_upload"
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.name == module_name, (
        "CAPABILITY ABSENCE: task_board_service.services.streaming_upload is missing"
    )
    module = importlib.import_module(module_name)
    limits_type = getattr(module, "AssetUploadLimits", None)
    stream_upload = getattr(module, "stream_asset_upload", None)
    assert callable(limits_type), (
        "CAPABILITY ABSENCE: streaming_upload.AssetUploadLimits is missing"
    )
    assert callable(stream_upload), (
        "CAPABILITY ABSENCE: streaming_upload.stream_asset_upload is missing"
    )
    return _Api(cast("_LimitsType", limits_type), cast("_StreamUpload", stream_upload))


def _limits(**updates: int) -> _Limits:
    values = {**_DEFAULT_LIMIT_VALUES, **updates}
    return _api().limits_type(**values)


def _file_request(
    boundary: str,
    content_chunks: list[bytes],
    *,
    filename: str = "evidence.txt",
    extra_headers: list[tuple[str, str]] | None = None,
    close: bool = True,
) -> list[bytes]:
    headers = [
        (
            "Content-Disposition",
            f'form-data; name="file"; filename="{filename}"',
        ),
        ("Content-Type", "text/plain"),
        *(extra_headers or []),
    ]
    chunks = [
        f"--{boundary}\r\n".encode(),
        *(f"{name}: {value}\r\n".encode() for name, value in headers),
        b"\r\n",
        *content_chunks,
    ]
    if close:
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return chunks


def _two_file_request(boundary: str) -> list[bytes]:
    chunks: list[bytes] = []
    for index in range(2):
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="file"; filename="evidence.txt"\r\n',
                b"Content-Type: text/plain\r\n\r\n",
                f"content-{index}".encode(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return chunks


def _unsigned_field_request(boundary: str) -> list[bytes]:
    return [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="unsigned"\r\n\r\n',
        b"untrusted\r\n",
        *_file_request(boundary, [b"valid"]),
    ]


def _request(boundary: str, chunks: list[bytes]) -> tuple[Request, _Receive]:
    receive = _Receive(chunks)
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/tasks/t-component/assets",
        "raw_path": b"/tasks/t-component/assets",
        "query_string": b"",
        "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    return Request(scope, receive=receive), receive


def _raw_size(chunks: list[bytes]) -> int:
    return sum(map(len, chunks))


def _overhead_size(chunks: list[bytes], file_size: int) -> int:
    overhead = _raw_size(chunks) - file_size
    assert overhead >= 0
    return overhead


def _assert_default_headroom(
    boundary: str,
    chunks: list[bytes],
    *,
    file_size: int,
) -> None:
    assert len(boundary.encode()) <= _DEFAULT_LIMIT_VALUES["max_boundary_bytes"]
    assert file_size <= _DEFAULT_LIMIT_VALUES["max_file_size"]
    assert _raw_size(chunks) <= _DEFAULT_LIMIT_VALUES["max_request_size"]
    assert (
        _overhead_size(chunks, file_size) <= _DEFAULT_LIMIT_VALUES["max_multipart_overhead_bytes"]
    )


async def _parse(
    limits: _Limits,
    boundary: str,
    chunks: list[bytes],
    *,
    expected_filename: str = "evidence.txt",
) -> tuple[_ParsedUpload | None, ServiceError | None, _Receive, _Sink]:
    request, receive = _request(boundary, chunks)
    sink = _Sink(receive)
    result: _ParsedUpload | None = None
    error: ServiceError | None = None
    try:
        result = await _api().stream_upload(
            request,
            limits,
            expected_filename,
            sink.write,
        )
    except ServiceError as exc:
        error = exc
    return result, error, receive, sink


def _assert_invalid_multipart(error: ServiceError | None) -> None:
    assert error is not None
    assert (error.status_code, error.error, error.details.get("reason")) == (
        400,
        "invalid_payload",
        "malformed_multipart",
    )


@pytest.mark.unit
def test_asset_upload_limits_support_explicit_alternate_values() -> None:
    """The isolated parser policy is strict, explicit and directly constructible."""
    assert all(type(value) is int and value > 0 for value in _DEFAULT_LIMIT_VALUES.values())
    limits = _limits()
    assert (
        limits.max_file_size,
        limits.max_request_size,
        limits.max_multipart_overhead_bytes,
        limits.stream_chunk_bytes,
        limits.max_header_count,
        limits.max_header_size,
        limits.max_boundary_bytes,
        limits.max_display_filename_utf8_bytes,
    ) == (64, 2_048, 1_024, 4, 4, 256, 8, 32)


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["missing", "zero", "boolean", "extra"])
def test_asset_upload_limits_reject_missing_invalid_and_extra_values(mutation: str) -> None:
    """Every bound is required, strictly positive, integer and extra-forbidden."""
    values: dict[str, object] = dict(_DEFAULT_LIMIT_VALUES)
    assert set(values) == {
        "max_file_size",
        "max_request_size",
        "max_multipart_overhead_bytes",
        "stream_chunk_bytes",
        "max_header_count",
        "max_header_size",
        "max_boundary_bytes",
        "max_display_filename_utf8_bytes",
    }
    api = _api()
    errors = (TypeError, ValueError, ValidationError)
    if mutation == "extra":
        with pytest.raises(errors):
            api.limits_type(**values, unknown_limit=1)  # type: ignore[arg-type]
        return
    replacement: object = True if mutation == "boolean" else 0
    for field in tuple(values):
        candidate = dict(values)
        if mutation == "missing":
            del candidate[field]
        else:
            candidate[field] = replacement
        with pytest.raises(errors):
            api.limits_type(**candidate)  # type: ignore[arg-type]


@pytest.mark.unit
async def test_valid_upload_streams_bounded_chunks_and_returns_exact_metadata() -> None:
    """A complete upload writes bounded slices and returns exact validated facts."""
    boundary = "12345678"
    content_chunks = [bytes([value]) * 16 for value in b"ABCDE"]
    content = b"".join(content_chunks)
    assert all(boundary.encode() not in chunk for chunk in content_chunks)
    prefix_chunk_count = len(_file_request(boundary, [], close=False))
    chunks = _file_request(boundary, content_chunks)
    assert len(boundary.encode()) == _DEFAULT_LIMIT_VALUES["max_boundary_bytes"]
    assert len(content) <= 128
    assert _raw_size(chunks) <= _DEFAULT_LIMIT_VALUES["max_request_size"]
    assert (
        _overhead_size(chunks, len(content))
        <= _DEFAULT_LIMIT_VALUES["max_multipart_overhead_bytes"]
    )
    limits = _limits(max_file_size=128)
    result, error, _receive, sink = await _parse(
        limits,
        boundary,
        chunks,
    )

    assert error is None
    assert result is not None
    assert all(0 < len(chunk) <= limits.stream_chunk_bytes for chunk in sink.chunks)
    assert b"".join(sink.chunks) == content
    lookbehind_allowance = len(boundary.encode()) + len(b"\r\n--")
    assert lookbehind_allowance == 12
    assert sum(map(len, content_chunks[-2:])) > lookbehind_allowance
    assert min(sink.receive_chunks_at_write) <= prefix_chunk_count + 1, (
        "the first content-chunk parser iteration did not flush before the next request chunk"
    )
    for received_content_chunks in range(1, len(content_chunks) + 1):
        receive_marker = prefix_chunk_count + received_content_chunks
        received_content_bytes = sum(
            len(chunk) for chunk in content_chunks[:received_content_chunks]
        )
        written_before_next_receive = sum(
            len(chunk)
            for chunk, marker in zip(
                sink.chunks,
                sink.receive_chunks_at_write,
                strict=True,
            )
            if marker <= receive_marker
        )
        assert 0 <= received_content_bytes - written_before_next_receive <= (lookbehind_allowance)
    assert (
        result.filename,
        result.content_type,
        result.size_bytes,
        result.content_hash,
    ) == ("evidence.txt", "text/plain", len(content), hashlib.sha256(content).hexdigest())


@pytest.mark.unit
@pytest.mark.parametrize("delta", [0, 1])
async def test_file_size_exact_limit_and_plus_one(delta: int) -> None:
    """Exact file size succeeds; plus one is bounded file_too_large."""
    boundary = "file-max"
    content = b"f" * (_DEFAULT_LIMIT_VALUES["max_file_size"] + delta)
    content_chunks = [content[index : index + 5] for index in range(0, len(content), 5)]
    chunks = _file_request(boundary, content_chunks)
    assert len(boundary.encode()) == _DEFAULT_LIMIT_VALUES["max_boundary_bytes"]
    assert _raw_size(chunks) <= _DEFAULT_LIMIT_VALUES["max_request_size"]
    assert (
        _overhead_size(chunks, len(content))
        <= _DEFAULT_LIMIT_VALUES["max_multipart_overhead_bytes"]
    )
    limits = _limits()
    result, error, receive, sink = await _parse(limits, boundary, chunks)

    if delta == 0:
        assert error is None and result is not None
        assert b"".join(sink.chunks) == content
    else:
        assert result is None
        assert error is not None
        assert (error.status_code, error.error) == (413, "file_too_large")
        prefix_size = sum(map(len, _file_request(boundary, [], close=False)))
        assert receive.yielded_bytes <= prefix_size + limits.max_file_size + 5


@pytest.mark.unit
@pytest.mark.parametrize("delta", [0, 1])
async def test_request_size_exact_limit_and_plus_one(delta: int) -> None:
    """Exact raw request size succeeds; plus one is bounded payload_too_large."""
    boundary = "request8"
    request_limit = 192
    if delta == 0:
        empty = _file_request(boundary, [])
        content_size = request_limit - _raw_size(empty)
        assert content_size > 0
        chunks = _file_request(boundary, [b"r" * content_size])
        assert _raw_size(chunks) == request_limit
        crossing_chunk_size = 0
    else:
        prefix = _file_request(boundary, [], close=False)
        content_before_crossing = request_limit - _raw_size(prefix)
        assert content_before_crossing > 0
        crossing_chunk = b"x" * 16
        sentinel_tail = [b"s" * 32] * 4
        chunks = [
            *prefix,
            b"r" * content_before_crossing,
            crossing_chunk,
            *sentinel_tail,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        assert _raw_size(chunks[: len(prefix) + 1]) == request_limit
        crossing_chunk_size = len(crossing_chunk)
    file_size = sum(len(chunk) for chunk in chunks if chunk and chunk[:1] in {b"r", b"x", b"s"})
    assert (
        _overhead_size(chunks, file_size) <= _DEFAULT_LIMIT_VALUES["max_multipart_overhead_bytes"]
    )
    limits = _limits(max_file_size=512, max_request_size=request_limit)
    result, error, receive, _sink = await _parse(limits, boundary, chunks)

    if delta == 0:
        assert error is None and result is not None
    else:
        assert result is None
        assert error is not None
        assert (error.status_code, error.error) == (413, "payload_too_large")
        assert receive.yielded_bytes <= request_limit + crossing_chunk_size
        assert receive.yielded_chunks < len(chunks)


@pytest.mark.unit
@pytest.mark.parametrize("delta", [0, 1])
async def test_multipart_overhead_exact_limit_and_plus_one(delta: int) -> None:
    """Multipart overhead is independent and exact; plus one is payload_too_large."""
    boundary = "overhead"
    content = b"ok"
    chunks = _file_request(boundary, [content])
    overhead = _overhead_size(chunks, len(content))
    assert overhead < _DEFAULT_LIMIT_VALUES["max_request_size"]
    overhead_limit = overhead - delta
    assert overhead_limit > 0
    limits = _limits(
        max_file_size=32,
        max_request_size=_raw_size(chunks) + 64,
        max_multipart_overhead_bytes=overhead_limit,
    )
    result, error, _receive, _sink = await _parse(limits, boundary, chunks)

    if delta == 0:
        assert error is None and result is not None
    else:
        assert result is None
        assert error is not None
        assert (error.status_code, error.error) == (413, "payload_too_large")


@pytest.mark.unit
@pytest.mark.parametrize("delta", [0, 1])
async def test_display_filename_utf8_bytes_exact_limit_and_plus_one(delta: int) -> None:
    """Exact UTF-8 filename bytes succeed; one byte above is invalid metadata."""
    filename_limit = _DEFAULT_LIMIT_VALUES["max_display_filename_utf8_bytes"]
    filename = "é" * (filename_limit // 2) + ("a" if delta else "")
    assert len(filename.encode("utf-8")) == filename_limit + delta
    boundary = "filename"
    chunks = _file_request(boundary, [b"valid"], filename=filename)
    assert _raw_size(chunks) <= _DEFAULT_LIMIT_VALUES["max_request_size"]
    assert _overhead_size(chunks, 5) <= _DEFAULT_LIMIT_VALUES["max_multipart_overhead_bytes"]
    limits = _limits()
    result, error, _receive, _sink = await _parse(
        limits,
        boundary,
        chunks,
        expected_filename=filename,
    )

    if delta == 0:
        assert error is None and result is not None
        assert result.filename == filename
    else:
        assert result is None
        assert error is not None
        assert (error.status_code, error.error, error.details.get("reason")) == (
            400,
            "invalid_payload",
            "invalid_filename",
        )


@pytest.mark.unit
async def test_display_filename_must_match_expected_signed_value() -> None:
    """A valid multipart filename cannot differ from the authenticated intent."""
    boundary = "mismatch"
    chunks = _file_request(boundary, [b"valid"], filename="actual.txt")
    _assert_default_headroom(boundary, chunks, file_size=5)
    limits = _limits()
    result, error, _receive, _sink = await _parse(
        limits,
        boundary,
        chunks,
        expected_filename="expected.txt",
    )

    assert result is None
    assert error is not None
    assert (error.status_code, error.error, error.details.get("reason")) == (
        400,
        "invalid_payload",
        "filename_mismatch",
    )


def _limit_case(
    kind: str,
    delta: int,
) -> tuple[str, list[tuple[str, str]]]:
    if kind == "boundary":
        return "b" * (_DEFAULT_LIMIT_VALUES["max_boundary_bytes"] + delta), []
    if kind == "header-count":
        count = _DEFAULT_LIMIT_VALUES["max_header_count"] - 2 + delta
        return "headers8", [(f"X-Test-{index}", "v") for index in range(count)]
    name = "X-Bound"
    prefix_size = len(f"{name}: ".encode())
    return "headers8", [
        (name, "v" * (_DEFAULT_LIMIT_VALUES["max_header_size"] + delta - prefix_size))
    ]


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["boundary", "header-count", "header-size"])
@pytest.mark.parametrize("delta", [0, 1])
async def test_parser_bounds_exact_limit_and_plus_one(kind: str, delta: int) -> None:
    """Every configurable parser bound accepts exact and rejects plus one."""
    boundary, headers = _limit_case(kind, delta)
    chunks = _file_request(boundary, [b"valid"], extra_headers=headers)
    header_count = 2 + len(headers)
    header_sizes = [len(f"{name}: {value}".encode()) for name, value in headers]
    if kind == "boundary":
        assert len(boundary.encode()) == _DEFAULT_LIMIT_VALUES["max_boundary_bytes"] + delta
        assert header_count < _DEFAULT_LIMIT_VALUES["max_header_count"]
        assert all(size < _DEFAULT_LIMIT_VALUES["max_header_size"] for size in header_sizes)
    elif kind == "header-count":
        assert header_count == _DEFAULT_LIMIT_VALUES["max_header_count"] + delta
        assert len(boundary.encode()) <= _DEFAULT_LIMIT_VALUES["max_boundary_bytes"]
        assert all(size < _DEFAULT_LIMIT_VALUES["max_header_size"] for size in header_sizes)
    else:
        assert header_count < _DEFAULT_LIMIT_VALUES["max_header_count"]
        assert header_sizes == [_DEFAULT_LIMIT_VALUES["max_header_size"] + delta]
        assert len(boundary.encode()) <= _DEFAULT_LIMIT_VALUES["max_boundary_bytes"]
    assert _overhead_size(chunks, 5) <= _DEFAULT_LIMIT_VALUES["max_multipart_overhead_bytes"]
    assert _raw_size(chunks) <= _DEFAULT_LIMIT_VALUES["max_request_size"]
    limits = _limits()
    result, error, _receive, _sink = await _parse(
        limits,
        boundary,
        chunks,
    )
    if delta == 0:
        assert error is None and result is not None
    else:
        assert result is None
        _assert_invalid_multipart(error)


@pytest.mark.unit
@pytest.mark.parametrize("case", ["truncated", "duplicate-file", "unsigned-field"])
async def test_invalid_framing_and_cardinality_use_one_exact_error(case: str) -> None:
    """Incomplete, duplicate and unsigned parts share the exact framing error."""
    boundary = "invalid8"
    if case == "truncated":
        chunks = _file_request(boundary, [b"partial"], close=False)
    elif case == "duplicate-file":
        chunks = _two_file_request(boundary)
    else:
        chunks = _unsigned_field_request(boundary)
    assert len(boundary.encode()) == _DEFAULT_LIMIT_VALUES["max_boundary_bytes"]
    assert _raw_size(chunks) <= _DEFAULT_LIMIT_VALUES["max_request_size"]
    file_size = {
        "truncated": len(b"partial"),
        "duplicate-file": len(b"content-0") + len(b"content-1"),
        "unsigned-field": len(b"valid"),
    }[case]
    assert (
        _overhead_size(chunks, file_size) <= _DEFAULT_LIMIT_VALUES["max_multipart_overhead_bytes"]
    )
    if case == "duplicate-file":
        body = b"".join(chunks)
        assert body.count(b'filename="evidence.txt"') == 2
        assert b"evidence-0.txt" not in body and b"evidence-1.txt" not in body
    limits = _limits(max_file_size=32)
    result, error, _receive, _sink = await _parse(limits, boundary, chunks)

    assert result is None
    _assert_invalid_multipart(error)
