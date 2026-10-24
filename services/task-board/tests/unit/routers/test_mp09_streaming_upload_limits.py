"""T-126 acceptance proofs for bounded streaming multipart uploads."""

from __future__ import annotations

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

_BOUNDARY_LIMIT = 70
_HEADER_COUNT_LIMIT = 8
_HEADER_SIZE_LIMIT = 4_224
_SMALL_FILE_LIMIT = 16


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded_chunks = 0
        self.yielded_bytes = 0

    @property
    def total_chunks(self) -> int:
        return len(self._chunks)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.yielded_chunks += 1
            self.yielded_bytes += len(chunk)
            yield chunk


@dataclass(frozen=True)
class _UploadSetup:
    task_id: str
    token: str
    storage_root: Path


def _file_chunks(
    boundary: str,
    *,
    content_chunks: list[bytes],
    extra_headers: list[tuple[str, str]] | None = None,
    closing_boundary: bool,
) -> list[bytes]:
    headers = [
        ("Content-Disposition", 'form-data; name="file"; filename="evidence.txt"'),
        ("Content-Type", "text/plain"),
        *(extra_headers or []),
    ]
    chunks = [f"--{boundary}\r\n".encode()]
    chunks.extend(f"{name}: {value}\r\n".encode() for name, value in headers)
    chunks.append(b"\r\n")
    chunks.extend(content_chunks)
    if closing_boundary:
        chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return chunks


def _two_file_chunks(boundary: str) -> list[bytes]:
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


def _unsigned_field_chunks(boundary: str) -> list[bytes]:
    return [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="unsigned"\r\n\r\n',
        b"untrusted metadata\r\n",
        *_file_chunks(
            boundary,
            content_chunks=[b"valid file"],
            closing_boundary=True,
        ),
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
    storage_root = tmp_path / "assets"
    assert _file_snapshot(storage_root) == {}
    state = get_app_state()
    assert state.store is not None
    assert state.store.count_assets(task_id) == 0
    return _UploadSetup(task_id, token, storage_root)


async def _post_stream(
    client: AsyncClient,
    setup: _UploadSetup,
    boundary: str,
    chunks: list[bytes],
) -> tuple[Response | None, BaseException | None, _CountingStream]:
    stream = _CountingStream(chunks)
    try:
        response = await client.post(
            f"/tasks/{setup.task_id}/assets",
            headers={
                "Authorization": f"Bearer {setup.token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            content=stream,
        )
    except BaseException as exc:
        return None, exc, stream
    return response, None, stream


def _assert_no_publication(setup: _UploadSetup) -> tuple[int, dict[str, bytes]]:
    state = get_app_state()
    assert state.store is not None
    return state.store.count_assets(setup.task_id), _file_snapshot(setup.storage_root)


@pytest.mark.unit
async def test_oversized_file_stops_consuming_request_before_tail_chunks(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    """Per-file overflow rejects before consuming an instrumented request tail."""
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    state = get_app_state()
    assert state.asset_manager is not None
    monkeypatch.setattr(state.asset_manager, "_max_file_size", _SMALL_FILE_LIMIT)
    boundary = "bounded-file"
    chunks = _file_chunks(
        boundary,
        content_chunks=[b"12345678"] * 8,
        closing_boundary=True,
    )

    response, error, stream = await _post_stream(client, setup, boundary, chunks)
    diagnostics = {
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "yielded_chunks": stream.yielded_chunks,
        "total_chunks": stream.total_chunks,
        "effects": _assert_no_publication(setup),
    }

    assert error is None and response is not None, diagnostics
    assert response.status_code == 413, diagnostics
    assert stream.yielded_chunks < stream.total_chunks, diagnostics
    assert _assert_no_publication(setup) == (0, {}), diagnostics


@pytest.mark.unit
async def test_total_request_limit_rejects_valid_file_with_oversized_epilogue(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    """Cumulative raw bytes are bounded even when the file itself is small."""
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary = "bounded-request"
    max_body_size = get_settings().request.max_body_size
    epilogue_chunk = b"e" * 65_536
    epilogue = [epilogue_chunk] * (max_body_size // len(epilogue_chunk) + 2)
    chunks = [
        *_file_chunks(
            boundary,
            content_chunks=[b"small file"],
            closing_boundary=True,
        ),
        *epilogue,
    ]

    response, error, stream = await _post_stream(client, setup, boundary, chunks)
    diagnostics = {
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "yielded_bytes": stream.yielded_bytes,
        "configured_limit": max_body_size,
        "yielded_chunks": stream.yielded_chunks,
        "total_chunks": stream.total_chunks,
        "effects": _assert_no_publication(setup),
    }

    assert error is None and response is not None, diagnostics
    assert response.status_code == 413, diagnostics
    assert stream.yielded_chunks < stream.total_chunks, diagnostics
    assert _assert_no_publication(setup) == (0, {}), diagnostics


@pytest.mark.unit
@pytest.mark.parametrize(
    ("case", "boundary", "extra_headers"),
    [
        ("boundary-limit-plus-one", "b" * (_BOUNDARY_LIMIT + 1), []),
        (
            "header-count-limit-plus-one",
            "header-count",
            [(f"X-Test-{index}", "v") for index in range(_HEADER_COUNT_LIMIT - 1)],
        ),
        (
            "header-size-limit-plus-one",
            "header-size",
            [("X-Oversized", "v" * (_HEADER_SIZE_LIMIT + 1))],
        ),
    ],
)
async def test_multipart_limit_plus_one_rejects_without_effects(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    case: str,
    boundary: str,
    extra_headers: list[tuple[str, str]],
) -> None:
    """Boundary, header-count and header-size limits reject before publication."""
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    chunks = _file_chunks(
        boundary,
        content_chunks=[b"valid file"],
        extra_headers=extra_headers,
        closing_boundary=True,
    )

    response, error, stream = await _post_stream(client, setup, boundary, chunks)
    diagnostics = {
        "case": case,
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "yielded_chunks": stream.yielded_chunks,
        "effects": _assert_no_publication(setup),
    }

    assert error is None and response is not None, diagnostics
    assert 400 <= response.status_code < 500, diagnostics
    assert _assert_no_publication(setup) == (0, {}), diagnostics


@pytest.mark.unit
@pytest.mark.parametrize("case", ["duplicate-file", "unsigned-field"])
async def test_malformed_multipart_rejects_without_effects(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    case: str,
) -> None:
    """Exactly one file and no unsigned metadata fields are accepted."""
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary = f"malformed-{case}"
    chunks = (
        _two_file_chunks(boundary) if case == "duplicate-file" else _unsigned_field_chunks(boundary)
    )

    response, error, stream = await _post_stream(client, setup, boundary, chunks)
    diagnostics = {
        "case": case,
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "yielded_chunks": stream.yielded_chunks,
        "effects": _assert_no_publication(setup),
    }

    assert error is None and response is not None, diagnostics
    assert 400 <= response.status_code < 500, diagnostics
    assert _assert_no_publication(setup) == (0, {}), diagnostics


@pytest.mark.unit
async def test_truncated_multipart_rejects_without_effects(
    client: AsyncClient,
    tmp_path: Path,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    """A missing closing boundary never publishes partial bytes or metadata."""
    setup = await _setup_upload(
        client,
        tmp_path,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    )
    boundary = "truncated-body"
    chunks = _file_chunks(
        boundary,
        content_chunks=[b"partial file bytes"],
        closing_boundary=False,
    )

    response, error, stream = await _post_stream(client, setup, boundary, chunks)
    diagnostics = {
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "yielded_chunks": stream.yielded_chunks,
        "effects": _assert_no_publication(setup),
    }

    assert error is None and response is not None, diagnostics
    assert 400 <= response.status_code < 500, diagnostics
    assert _assert_no_publication(setup) == (0, {}), diagnostics
