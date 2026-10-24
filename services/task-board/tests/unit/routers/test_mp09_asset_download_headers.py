"""T-126 acceptance proofs for safe artifact download headers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import setup_task_in_execution

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response


_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_HEX = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True)
class _HttpParameter:
    name: str
    value: str
    quoted: bool


def _skip_ows(value: str, offset: int) -> int:
    while offset < len(value) and value[offset] in " \t":
        offset += 1
    return offset


def _token_at(value: str, offset: int) -> tuple[str, int]:
    match = _TOKEN.match(value, offset)
    if match is None:
        raise ValueError(f"expected HTTP token at offset {offset}")
    return match.group(), match.end()


def _quoted_string_at(value: str, offset: int) -> tuple[str, int]:
    if offset >= len(value) or value[offset] != '"':
        raise ValueError(f"expected quoted string at offset {offset}")
    offset += 1
    decoded: list[str] = []
    while offset < len(value):
        character = value[offset]
        if character == '"':
            return "".join(decoded), offset + 1
        if character == "\\":
            offset += 1
            if offset >= len(value):
                raise ValueError("unterminated HTTP quoted-pair")
            character = value[offset]
        if (ord(character) < 0x20 and character != "\t") or ord(character) == 0x7F:
            raise ValueError("control character in HTTP quoted-string")
        decoded.append(character)
        offset += 1
    raise ValueError("unterminated HTTP quoted-string")


def _parse_http_content_disposition(value: str) -> tuple[str, list[_HttpParameter]]:
    """Parse the RFC 6266 subset needed by the download response contract."""
    offset = _skip_ows(value, 0)
    disposition, offset = _token_at(value, offset)
    parameters: list[_HttpParameter] = []
    while True:
        offset = _skip_ows(value, offset)
        if offset == len(value):
            return disposition.lower(), parameters
        if value[offset] != ";":
            raise ValueError(f"unconsumed Content-Disposition text at offset {offset}")
        offset = _skip_ows(value, offset + 1)
        name, offset = _token_at(value, offset)
        offset = _skip_ows(value, offset)
        if offset >= len(value) or value[offset] != "=":
            raise ValueError(f"missing parameter equals sign at offset {offset}")
        offset = _skip_ows(value, offset + 1)
        if offset < len(value) and value[offset] == '"':
            parameter_value, offset = _quoted_string_at(value, offset)
            quoted = True
        else:
            parameter_value, offset = _token_at(value, offset)
            quoted = False
        parameters.append(_HttpParameter(name.lower(), parameter_value, quoted))


def _decode_extended_filename(value: str) -> str:
    prefix = "UTF-8''"
    if not value.startswith(prefix):
        raise ValueError("filename* must use UTF-8 with an empty language")
    encoded = value[len(prefix) :]
    decoded = bytearray()
    offset = 0
    while offset < len(encoded):
        character = encoded[offset]
        if character != "%":
            codepoint = ord(character)
            if codepoint > 0x7F:
                raise ValueError("filename* contains a non-ASCII raw character")
            decoded.append(codepoint)
            offset += 1
            continue
        if (
            offset + 2 >= len(encoded)
            or encoded[offset + 1] not in _HEX
            or encoded[offset + 2] not in _HEX
        ):
            raise ValueError("filename* contains an invalid percent escape")
        decoded.append(int(encoded[offset + 1 : offset + 3], 16))
        offset += 3
    return bytes(decoded).decode("utf-8", errors="strict")


async def _download_named_asset(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
    filename: str,
) -> tuple[Response | None, BaseException | None, bytes]:
    task_id, _bid_id = await setup_task_in_execution(
        client,
        poster_keypair,
        poster_id,
        worker_keypair,
        worker_id,
    )
    content = f"content for {filename}".encode()
    state = get_app_state()
    assert state.asset_manager is not None
    assert state.store is not None
    token = make_jws_token(
        worker_keypair[0],
        worker_id,
        {"action": "upload_asset", "task_id": task_id},
    )
    upload = await state.asset_manager.upload_asset(
        task_id,
        token,
        content,
        filename,
        "application/octet-stream",
    )
    assert upload["filename"] == filename, (
        "SETUP FAILURE: AssetManager did not preserve the exact raw display filename"
    )
    persisted = state.store.get_asset(str(upload["asset_id"]), task_id)
    assert persisted is not None
    assert persisted["filename"] == filename, (
        "SETUP FAILURE: persisted metadata did not preserve the exact raw display filename"
    )
    try:
        response = await client.get(f"/tasks/{task_id}/assets/{upload['asset_id']}")
    except BaseException as exc:
        return None, exc, content
    return response, None, content


@pytest.mark.unit
@pytest.mark.parametrize(
    "filename",
    [
        'report; "final".txt',
        "résultat-日本語.txt",
        "literal%2B+100%.txt",
    ],
)
async def test_download_filename_roundtrips_without_extra_parameters_or_500(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    filename: str,
) -> None:
    """Quotes and Unicode round-trip with only filename parameters and safe headers."""
    response, error, expected_content = await _download_named_asset(
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        filename,
    )
    diagnostics = {
        "filename": filename,
        "exception": None if error is None else repr(error),
        "status": None if response is None else response.status_code,
        "headers": None if response is None else dict(response.headers),
    }

    assert error is None and response is not None, diagnostics
    assert response.status_code == 200, diagnostics
    assert response.content == expected_content, diagnostics
    disposition = response.headers["content-disposition"]
    try:
        disposition_type, parameters = _parse_http_content_disposition(disposition)
    except ValueError as exc:
        pytest.fail(f"invalid HTTP Content-Disposition: {exc}; diagnostics={diagnostics}")
    assert disposition_type == "attachment", diagnostics
    assert [parameter.name for parameter in parameters] == ["filename", "filename*"], diagnostics
    fallback, extended = parameters
    assert fallback.quoted, diagnostics
    assert fallback.value and fallback.value.isascii(), diagnostics
    assert not extended.quoted, diagnostics
    assert _decode_extended_filename(extended.value) == filename, diagnostics
    if filename.isascii():
        assert fallback.value == filename, diagnostics
    assert response.headers.get("cache-control") == "no-store", diagnostics
    assert response.headers.get("x-content-type-options") == "nosniff", diagnostics
