"""Reviewed T-126 contract for configurable streamed multipart admission."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import pytest

from task_board_service.config import get_settings
from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import setup_task_in_execution

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response

_INVALID_MULTIPART_ERROR = "invalid_payload"
_INVALID_MULTIPART_REASON = "malformed_multipart"


@dataclass(frozen=True)
class _UploadBounds:
    max_file_bytes: int
    max_request_bytes: int
    stream_chunk_bytes: int
    max_multipart_overhead_bytes: int
    max_boundary_bytes: int
    max_header_count: int
    max_header_bytes: int
    max_display_filename_utf8_bytes: int


@dataclass(frozen=True)
class _UploadSetup:
    task_id: str
    token: str
    storage_root: Path


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded_chunks = 0
        self.yielded_bytes = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.yielded_chunks += 1
            self.yielded_bytes += len(chunk)
            yield chunk


def _bounds() -> _UploadBounds:
    settings = get_settings()
    if settings.assets is not None:
        max_file_bytes = settings.assets.max_file_size
    else:
        assert settings.limits is not None
        max_file_bytes = settings.limits.max_file_size
    return _UploadBounds(
        max_file_bytes=max_file_bytes,
        max_request_bytes=settings.request.max_body_size,
        stream_chunk_bytes=262_144,
        max_multipart_overhead_bytes=1_048_576,
        max_boundary_bytes=70,
        max_header_count=8,
        max_header_bytes=4_224,
        max_display_filename_utf8_bytes=255,
    )


def _file_part_prefix(
    boundary: str,
    *,
    extra_headers: list[tuple[str, str]] | None = None,
) -> list[bytes]:
    headers = [
        ("Content-Disposition", 'form-data; name="file"; filename="evidence.txt"'),
        ("Content-Type", "text/plain"),
        *(extra_headers or []),
    ]
    return [
        f"--{boundary}\r\n".encode(),
        *(f"{name}: {value}\r\n".encode() for name, value in headers),
        b"\r\n",
    ]


def _file_request(
    boundary: str,
    *,
    content_chunks: list[bytes],
    extra_headers: list[tuple[str, str]] | None = None,
    close: bool = True,
) -> list[bytes]:
    chunks = [*_file_part_prefix(boundary, extra_headers=extra_headers), *content_chunks]
    if close:
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return chunks


def _two_file_request(boundary: str) -> list[bytes]:
    chunks: list[bytes] = []
    for index in range(2):
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="file"; '
                    f'filename="evidence-{index}.txt"\r\n'
                ).encode(),
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
        *_file_request(boundary, content_chunks=[b"valid file"]),
    ]


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


async def _setup_upload(
    client: AsyncClient,
    tmp_path: Path,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
) -> _UploadSetup:
    task_id, _bid_id = await setup_task_in_execution(
        client,
        poster_keypair,
        poster_id,
        worker_keypair,
        worker_id,
    )
    token = make_jws_token(
        worker_keypair[0],
        worker_id,
        {"action": "upload_asset", "task_id": task_id},
    )
    setup = _UploadSetup(task_id, token, tmp_path / "assets")
    assert _effects(setup) == (0, {})
    return setup


def _effects(setup: _UploadSetup) -> tuple[int, dict[str, bytes]]:
    state = get_app_state()
    assert state.store is not None
    return state.store.count_assets(setup.task_id), _file_snapshot(setup.storage_root)


def _effect_summary(setup: _UploadSetup) -> dict[str, object]:
    count, files = _effects(setup)
    return {
        "asset_count": count,
        "files": {
            path: {"size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in files.items()
        },
    }


async def _post(
    client: AsyncClient,
    setup: _UploadSetup,
    boundary: str,
    chunks: list[bytes],
    *,
    token: str | None = None,
) -> tuple[Response | None, BaseException | None, _CountingStream]:
    stream = _CountingStream(chunks)
    try:
        response = await client.post(
            f"/tasks/{setup.task_id}/assets",
            headers={
                "Authorization": f"Bearer {setup.token if token is None else token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            content=stream,
        )
    except BaseException as exc:
        return None, exc, stream
    return response, None, stream


def _response_error(response: Response) -> str | None:
    data = response.json()
    return data.get("error") if isinstance(data, dict) else None


def _response_reason(response: Response) -> str | None:
    data = response.json()
    if not isinstance(data, dict):
        return None
    details = data.get("details")
    if not isinstance(details, dict):
        return None
    reason = details.get("reason")
    return reason if isinstance(reason, str) else None


@pytest.mark.unit
async def test_file_overflow_consumption_is_bounded_by_framing_plus_one_chunk(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    """File overflow consumes at most exact prefix, file limit and crossing chunk."""
    settings = get_settings()
    if settings.assets is not None:
        selected_file_limit = settings.assets.max_file_size
    else:
        assert settings.limits is not None
        selected_file_limit = settings.limits.max_file_size
    monkeypatch.setattr(
        settings.request,
        "max_body_size",
        selected_file_limit
        + settings.asset_upload.max_multipart_overhead_bytes
        + settings.asset_upload.stream_chunk_bytes,
    )
    bounds = _bounds()
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary = "file-overflow"
    prefix = _file_part_prefix(boundary)
    full_chunks, remainder = divmod(bounds.max_file_bytes, bounds.stream_chunk_bytes)
    exact_file_chunks = [b"f" * bounds.stream_chunk_bytes] * full_chunks
    if remainder:
        exact_file_chunks.append(b"r" * remainder)
    crossing_chunk = b"x" * bounds.stream_chunk_bytes
    untouched_tail = b"tail must remain unread"
    chunks = [
        *prefix,
        *exact_file_chunks,
        crossing_chunk,
        untouched_tail,
        f"\r\n--{boundary}--\r\n".encode(),
    ]

    response, error, stream = await _post(client, setup, boundary, chunks)
    raw_budget = sum(map(len, prefix)) + bounds.max_file_bytes + len(crossing_chunk)
    diagnostics = {
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "error": None if response is None else _response_error(response),
        "yielded_bytes": stream.yielded_bytes,
        "raw_budget": raw_budget,
        "yielded_chunks": stream.yielded_chunks,
        "total_chunks": len(chunks),
        "effects": _effect_summary(setup),
    }

    assert error is None and response is not None, diagnostics
    assert (response.status_code, _response_error(response)) == (413, "file_too_large")
    assert stream.yielded_bytes <= raw_budget, diagnostics
    assert stream.yielded_chunks < len(chunks), diagnostics
    assert _effects(setup) == (0, {}), diagnostics


@pytest.mark.unit
@pytest.mark.parametrize("delta", [0, 1])
async def test_total_request_exact_limit_and_plus_one(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    delta: int,
) -> None:
    """Exact request limit succeeds; one raw byte above it is payload_too_large."""
    bounds = _bounds()
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary = f"request-{delta}"
    empty = _file_request(boundary, content_chunks=[])
    framing_bytes = sum(map(len, empty))
    assert framing_bytes <= bounds.max_multipart_overhead_bytes
    content_size = bounds.max_request_bytes + delta - framing_bytes
    assert 0 <= content_size <= bounds.max_file_bytes
    chunks = _file_request(boundary, content_chunks=[b"r" * content_size])
    assert sum(map(len, chunks)) == bounds.max_request_bytes + delta

    response, error, stream = await _post(client, setup, boundary, chunks)
    diagnostics = {
        "delta": delta,
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "error": None if response is None else _response_error(response),
        "yielded_bytes": stream.yielded_bytes,
        "effects": _effect_summary(setup),
    }

    assert error is None and response is not None, diagnostics
    if delta == 0:
        assert response.status_code == 201, diagnostics
        assert _effects(setup)[0] == 1, diagnostics
    else:
        assert (response.status_code, _response_error(response)) == (
            413,
            "payload_too_large",
        ), diagnostics
        assert stream.yielded_bytes <= bounds.max_request_bytes + len(chunks[-2]), diagnostics
        assert _effects(setup) == (0, {}), diagnostics


def _limit_case(
    bounds: _UploadBounds,
    kind: str,
    delta: int,
) -> tuple[str, list[tuple[str, str]]]:
    if kind == "boundary":
        return "b" * (bounds.max_boundary_bytes + delta), []
    if kind == "header-count":
        extra_count = bounds.max_header_count - 2 + delta
        return "header-count", [(f"X-Test-{index}", "v") for index in range(extra_count)]
    if kind == "header-size":
        name = "X-Bound"
        prefix_size = len(f"{name}: ".encode())
        value_size = bounds.max_header_bytes + delta - prefix_size
        return "header-size", [(name, "v" * value_size)]
    raise AssertionError(f"unknown limit kind: {kind}")


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["boundary", "header-count", "header-size"])
@pytest.mark.parametrize("delta", [0, 1])
async def test_multipart_parser_exact_limit_and_plus_one(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    kind: str,
    delta: int,
) -> None:
    """Boundary and part-header bounds accept exact values and reject plus one."""
    bounds = _bounds()
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary, headers = _limit_case(bounds, kind, delta)
    chunks = _file_request(
        boundary,
        content_chunks=[b"valid file"],
        extra_headers=headers,
    )

    response, error, _stream = await _post(client, setup, boundary, chunks)
    diagnostics = {
        "kind": kind,
        "delta": delta,
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "error": None if response is None else _response_error(response),
        "reason": None if response is None else _response_reason(response),
        "effects": _effect_summary(setup),
    }

    assert error is None and response is not None, diagnostics
    if delta == 0:
        assert response.status_code == 201, diagnostics
        assert _effects(setup)[0] == 1, diagnostics
    else:
        assert (response.status_code, _response_error(response), _response_reason(response)) == (
            400,
            _INVALID_MULTIPART_ERROR,
            _INVALID_MULTIPART_REASON,
        ), diagnostics
        assert _effects(setup) == (0, {}), diagnostics


@pytest.mark.unit
@pytest.mark.parametrize("case", ["truncated", "duplicate-file", "unsigned-field"])
async def test_invalid_multipart_has_one_exact_error_and_no_effects(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    case: str,
) -> None:
    """Malformed framing uses invalid_payload with one explicit details reason."""
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary = f"invalid-{case}"
    if case == "truncated":
        chunks = _file_request(boundary, content_chunks=[b"partial"], close=False)
    elif case == "duplicate-file":
        chunks = _two_file_request(boundary)
    else:
        chunks = _unsigned_field_request(boundary)

    response, error, _stream = await _post(client, setup, boundary, chunks)
    diagnostics = {
        "case": case,
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "error": None if response is None else _response_error(response),
        "reason": None if response is None else _response_reason(response),
        "effects": _effect_summary(setup),
    }

    assert error is None and response is not None, diagnostics
    assert (response.status_code, _response_error(response), _response_reason(response)) == (
        400,
        _INVALID_MULTIPART_ERROR,
        _INVALID_MULTIPART_REASON,
    ), diagnostics
    assert _effects(setup) == (0, {}), diagnostics


@pytest.mark.unit
async def test_invalid_authentication_is_rejected_before_file_body_consumption(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    """Malformed JWS admission completes before the first multipart chunk is read."""
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary = "auth-first"
    chunks = _file_request(boundary, content_chunks=[b"must remain unread"] * 4)

    response, error, stream = await _post(
        client,
        setup,
        boundary,
        chunks,
        token="not-a-compact-jws",
    )
    diagnostics = {
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "error": None if response is None else _response_error(response),
        "yielded_chunks": stream.yielded_chunks,
        "effects": _effect_summary(setup),
    }

    assert error is None and response is not None, diagnostics
    assert (response.status_code, _response_error(response)) == (400, "invalid_jws")
    assert stream.yielded_chunks == 0, diagnostics
    assert _effects(setup) == (0, {}), diagnostics


@pytest.mark.unit
async def test_valid_multichunk_upload_preserves_exact_hash_and_bytes(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    """A valid multi-chunk request publishes and downloads the exact byte sequence."""
    bounds = _bounds()
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary = "valid-multichunk"
    content_chunks = [
        b"a" * bounds.stream_chunk_bytes,
        b"b" * bounds.stream_chunk_bytes,
        b"final-chunk",
    ]
    expected = b"".join(content_chunks)
    chunks = _file_request(boundary, content_chunks=content_chunks)

    response, error, stream = await _post(client, setup, boundary, chunks)
    diagnostics = {
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "yielded_chunks": stream.yielded_chunks,
        "total_chunks": len(chunks),
        "effects": _effect_summary(setup),
    }

    assert error is None and response is not None, diagnostics
    assert response.status_code == 201, diagnostics
    result = response.json()
    assert result["size_bytes"] == len(expected), diagnostics
    assert result["content_hash"] == hashlib.sha256(expected).hexdigest(), diagnostics
    state = get_app_state()
    assert state.asset_manager is not None
    downloaded, content_type, filename = await state.asset_manager.download_asset(
        setup.task_id,
        str(result["asset_id"]),
    )
    assert (downloaded, content_type, filename) == (
        expected,
        "text/plain",
        "evidence.txt",
    ), diagnostics
