"""Each Gateway domain rejects invalid authority without changing any stored row."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import permutations
from typing import TYPE_CHECKING

import pytest
from service_auth import jws
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair

from tests.unit.test_mp02_gateway_authority import (
    _seed_domain_graph,
    _signed_authorization,
    _write_case,
)

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit

_HANDLES = {
    "identity": "gateway-identity",
    "bank": "gateway-central-bank",
    "board": "gateway-task-board",
    "reputation": "gateway-reputation",
    "court": "gateway-court",
}


@pytest.mark.parametrize(("caller", "domain"), list(permutations(_HANDLES, 2)))
def test_every_service_key_is_denied_every_other_domains_writes(
    caller: str,
    domain: str,
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    db_writer: DbWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _seed_domain_graph(app_with_writer, monkeypatch)
    path, data, _table = _write_case(domain, graph)
    body = json.dumps(data, separators=(",", ":")).encode()
    authorization = _signed_authorization(
        gateway_test_signers[caller],
        "POST",
        path,
        action=f"gateway.{domain}.write",
        body=body,
    )
    before = tuple(db_writer.connection.iterdump())

    response = app_with_writer.post(
        path,
        content=body,
        headers={"Authorization": authorization, "Content-Type": "application/json"},
        auth=None,
    )

    assert response.status_code == 403, response.text
    assert tuple(db_writer.connection.iterdump()) == before


@pytest.mark.parametrize("domain", list(_HANDLES))
@pytest.mark.parametrize(
    ("fault", "expected_status", "issued_offset"),
    [
        ("wrong-domain", 403, 0),
        ("unknown-key", 403, 0),
        ("bad-signature", 403, 0),
        ("expired", 401, -31),
        ("future", 401, 1),
        ("body-changed", 400, 0),
        ("query-changed", 400, 0),
    ],
)
def test_invalid_domain_write_has_no_effect_then_valid_control_succeeds(
    domain: str,
    fault: str,
    expected_status: int,
    issued_offset: int,
    app_with_writer: TestClient,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    db_writer: DbWriter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(jws, "_clock", lambda: now)
    graph = _seed_domain_graph(app_with_writer, monkeypatch)
    path, data, table = _write_case(domain, graph)
    body = json.dumps(data, separators=(",", ":")).encode()
    signer = gateway_test_signers[domain]
    if fault == "wrong-domain":
        signer = gateway_test_signers["bank" if domain == "identity" else "identity"]
    if fault in {"unknown-key", "bad-signature"}:
        key_dir = tmp_path / "untrusted"
        key_dir.mkdir()
        handle = "gateway-unknown" if fault == "unknown-key" else _HANDLES[domain]
        generate_keypair(handle, key_dir)
        signer = GatewayRequestSigner(
            GatewaySignerConfig(
                kid=handle,
                private_key_path=key_dir / f"{handle}.key",
                audience="db-gateway",
                token_ttl_seconds=30,
            ),
            config_dir=key_dir,
        )
    monkeypatch.setattr(jws, "_clock", lambda: now + timedelta(seconds=issued_offset))
    authorization = _signed_authorization(
        signer, "POST", path, action=f"gateway.{domain}.write", body=body
    )
    monkeypatch.setattr(jws, "_clock", lambda: now)
    before = tuple(db_writer.connection.iterdump())
    count_before = db_writer.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    response = app_with_writer.post(
        path + ("?changed=1" if fault == "query-changed" else ""),
        content=body + (b" " if fault == "body-changed" else b""),
        headers={"Authorization": authorization, "Content-Type": "application/json"},
        auth=None,
    )

    assert response.status_code == expected_status, response.text
    assert tuple(db_writer.connection.iterdump()) == before
    valid_authorization = _signed_authorization(
        gateway_test_signers[domain],
        "POST",
        path,
        action=f"gateway.{domain}.write",
        body=body,
    )
    control = app_with_writer.post(
        path,
        content=body,
        headers={"Authorization": valid_authorization, "Content-Type": "application/json"},
        auth=None,
    )
    assert control.status_code == (200 if domain in {"bank", "board"} else 201), control.text
    assert (
        db_writer.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        == count_before + 1
    )
