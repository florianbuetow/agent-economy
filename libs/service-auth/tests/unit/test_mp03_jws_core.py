"""Strict MP-03 JWS core acceptance tests for the shared verifier."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth import jws
from service_auth.jws import (
    ForbiddenError,
    InvalidJwsError,
    InvalidPayloadError,
    JwsProtectedHeader,
    TokenExpiredError,
    TokenNotYetValidError,
    TokenPolicy,
    UpstreamContractError,
    VerifiedJws,
    create_jws,
    parse_identity_verification_response,
    parse_jws,
    require_context,
    require_signer,
    verify_jws,
)
from service_auth.signing import PlatformSigner, generate_keypair

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_893_456_000
POLICY = TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30)
HEADER = {
    "alg": "EdDSA",
    "typ": "JWT",
    "kid": "a-platform",
    "iat": NOW,
    "exp": NOW + 300,
    "aud": "central-bank",
    "resource": "POST /escrow/esc-1/release",
}
PAYLOAD = {"action": "escrow_release", "escrow_id": "esc-1"}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _raw_token(
    header: object,
    payload: object,
    key: Ed25519PrivateKey,
    *,
    header_json: str | None = None,
    payload_json: str | None = None,
) -> str:
    header_bytes = (
        header_json.encode()
        if header_json is not None
        else json.dumps(header, separators=(",", ":")).encode()
    )
    payload_bytes = (
        payload_json.encode()
        if payload_json is not None
        else json.dumps(payload, separators=(",", ":")).encode()
    )
    first = _b64(header_bytes)
    second = _b64(payload_bytes)
    signed = f"{first}.{second}".encode("ascii")
    return f"{first}.{second}.{_b64(key.sign(signed))}"


def _verify_raw(
    key: Ed25519PrivateKey,
    *,
    header: object = HEADER,
    payload: object = PAYLOAD,
    header_json: str | None = None,
    payload_json: str | None = None,
) -> VerifiedJws:
    return verify_jws(
        _raw_token(header, payload, key, header_json=header_json, payload_json=payload_json),
        key.public_key(),
        policy=POLICY,
    )


@pytest.fixture(autouse=True)
def _clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jws, "_clock", lambda: datetime.fromtimestamp(NOW, tz=UTC))


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.mark.unit
def test_create_and_verify_roundtrip_is_typed_and_keeps_context_in_header(
    key: Ed25519PrivateKey,
) -> None:
    token = create_jws(
        PAYLOAD,
        key,
        kid="a-platform",
        audience="central-bank",
        resource="POST /escrow/esc-1/release",
        token_ttl_seconds=300,
    )
    result = verify_jws(token, key.public_key(), policy=POLICY)
    assert result == VerifiedJws(
        header=JwsProtectedHeader(**HEADER),
        payload=PAYLOAD,
    )
    assert set(json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))) == set(HEADER)
    assert "aud" not in result.payload
    assert "resource" not in result.payload


@pytest.mark.unit
def test_signature_covers_received_compact_bytes_without_reserializing(
    key: Ed25519PrivateKey,
) -> None:
    header_json = json.dumps(HEADER, indent=1)
    payload_json = '{"escrow_id":"esc-1", "action":"escrow_release"}'
    result = _verify_raw(key, header_json=header_json, payload_json=payload_json)
    assert result.payload == PAYLOAD


@pytest.mark.unit
def test_wrong_signature_remains_cryptography_invalid_signature(
    key: Ed25519PrivateKey,
) -> None:
    other = Ed25519PrivateKey.generate()
    token = _raw_token(HEADER, PAYLOAD, key)
    with pytest.raises(InvalidSignature):
        verify_jws(token, other.public_key(), policy=POLICY)


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, [], "x", 1, True])
def test_header_and_payload_must_be_objects(value: object, key: Ed25519PrivateKey) -> None:
    with pytest.raises(InvalidJwsError, match="JSON object") as header_error:
        _verify_raw(key, header=value)
    assert (header_error.value.status_code, header_error.value.error_code) == (
        400,
        "invalid_jws",
    )
    with pytest.raises(InvalidJwsError, match="JSON object"):
        _verify_raw(key, payload=value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("alg", "HS256"),
        ("alg", True),
        ("typ", "JWS"),
        ("typ", 1),
        ("kid", " "),
        ("kid", True),
        ("iat", True),
        ("iat", "1"),
        ("exp", True),
        ("exp", 1.5),
        ("aud", ""),
        ("aud", 7),
        ("resource", "get /tasks/1"),
        ("resource", "GET https://example.test/tasks/1"),
        ("resource", "GET /tasks/1?q=x"),
        ("resource", "GET /tasks/1#x"),
        ("resource", "GET  /tasks/1"),
        ("resource", "GET /tasks/{id}"),
    ],
)
def test_rejects_wrong_header_field_values(
    field: str,
    value: object,
    key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(InvalidJwsError):
        _verify_raw(key, header={**HEADER, field: value})


@pytest.mark.unit
@pytest.mark.parametrize("field", list(HEADER))
def test_rejects_every_missing_header_field(field: str, key: Ed25519PrivateKey) -> None:
    header = dict(HEADER)
    del header[field]
    with pytest.raises(InvalidJwsError):
        _verify_raw(key, header=header)


@pytest.mark.unit
def test_rejects_extra_header_field(key: Ed25519PrivateKey) -> None:
    with pytest.raises(InvalidJwsError, match="exactly"):
        _verify_raw(key, header={**HEADER, "extra": "no"})


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{"escrow_id": "x"}, {"action": ""}, {"action": 4}])
def test_payload_requires_nonblank_string_action(
    payload: dict[str, object],
    key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(InvalidPayloadError):
        _verify_raw(key, payload=payload)


@pytest.mark.unit
def test_action_only_payload_and_same_named_domain_fields_are_valid(
    key: Ed25519PrivateKey,
) -> None:
    action_only = {"action": "balance"}
    token = create_jws(
        action_only,
        key,
        kid="a-platform",
        audience="central-bank",
        resource="GET /accounts/a-1/balance",
        token_ttl_seconds=300,
    )
    assert verify_jws(token, key.public_key(), policy=POLICY).payload == action_only
    payload_context_names = {
        "action": "history",
        "aud": "court",
        "resource": "GET /claims/c-1",
    }
    verified = _verify_raw(key, payload=payload_context_names)
    assert verified.payload == payload_context_names
    with pytest.raises(InvalidPayloadError):
        require_context(
            verified,
            audience="court",
            resource="GET /claims/c-1",
            allowed_action="history",
        )


@pytest.mark.unit
def test_rejects_duplicate_and_nonfinite_json(key: Ed25519PrivateKey) -> None:
    with pytest.raises(InvalidJwsError, match="duplicate"):
        _verify_raw(
            key,
            header_json='{"alg":"EdDSA","alg":"EdDSA","typ":"JWT","kid":"a",'
            f'"iat":{NOW},"exp":{NOW + 1},"aud":"x","resource":"GET /x"}}',
        )
    with pytest.raises(InvalidJwsError, match="finite"):
        _verify_raw(key, payload_json='{"action":"x","value":NaN}')
    with pytest.raises(InvalidJwsError, match="finite"):
        _verify_raw(key, payload_json='{"action":"x","nested":{"value":1e400}}')


@pytest.mark.unit
@pytest.mark.parametrize("decoder_error", [ValueError("decoder"), RecursionError("deep")])
def test_decoder_failures_are_normalized_to_invalid_jws(
    decoder_error: Exception,
    key: Ed25519PrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _raw_token(HEADER, PAYLOAD, key)

    def fail_decode(*_args: object, **_kwargs: object) -> object:
        raise decoder_error

    monkeypatch.setattr(jws.json, "loads", fail_decode)
    with pytest.raises(InvalidJwsError, match="malformed JSON"):
        parse_jws(token)


@pytest.mark.unit
@pytest.mark.parametrize("shape", ["deep", "cycle"])
def test_create_jws_normalizes_recursive_payloads(
    shape: str,
    key: Ed25519PrivateKey,
) -> None:
    payload: dict[str, object] = {"action": "x"}
    if shape == "deep":
        nested: object = None
        for _index in range(1_100):
            nested = [nested]
        payload["value"] = nested
    else:
        cycle: list[object] = []
        cycle.append(cycle)
        payload["value"] = cycle

    with pytest.raises(InvalidJwsError, match="recursive"):
        create_jws(
            payload,
            key,
            kid="a-platform",
            audience="central-bank",
            resource="POST /operations/x",
            token_ttl_seconds=300,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "token",
    ["two.parts", "...", "a.b.c.d", "@@@.e30.AA", "e30.e30.@@@"],
)
def test_malformed_compact_or_base64_is_clean_invalid_jws(
    token: str,
    key: Ed25519PrivateKey,
) -> None:
    with pytest.raises(InvalidJwsError):
        verify_jws(token, key.public_key(), policy=POLICY)


@pytest.mark.unit
@pytest.mark.parametrize("length", [1, 63, 65])
def test_signature_must_decode_to_exact_ed25519_length(
    length: int,
    key: Ed25519PrivateKey,
) -> None:
    valid = _raw_token(HEADER, PAYLOAD, key)
    header, payload, _signature = valid.split(".")
    malformed = f"{header}.{payload}.{_b64(bytes(length))}"
    with pytest.raises(InvalidJwsError, match="64 bytes"):
        verify_jws(malformed, key.public_key(), policy=POLICY)


@pytest.mark.unit
def test_valid_length_wrong_signature_remains_invalid_signature(
    key: Ed25519PrivateKey,
) -> None:
    valid = _raw_token(HEADER, PAYLOAD, key)
    header, payload, _signature = valid.split(".")
    with pytest.raises(InvalidSignature):
        verify_jws(f"{header}.{payload}.{_b64(bytes(64))}", key.public_key(), policy=POLICY)


@pytest.mark.unit
def test_token_policy_rejects_bool_and_invalid_ranges() -> None:
    for maximum, skew in [(0, 0), (-1, 0), (True, 0), (1, -1), (1, True)]:
        with pytest.raises(ValueError):
            TokenPolicy(max_lifetime_seconds=maximum, clock_skew_seconds=skew)  # type: ignore[arg-type]


@pytest.mark.unit
def test_temporal_edges_and_maximum_lifetime(key: Ed25519PrivateKey) -> None:
    assert _verify_raw(key, header={**HEADER, "iat": NOW + 30, "exp": NOW + 31})
    with pytest.raises(TokenNotYetValidError) as future:
        _verify_raw(key, header={**HEADER, "iat": NOW + 31, "exp": NOW + 32})
    assert (future.value.status_code, future.value.error_code) == (
        401,
        "token_not_yet_valid",
    )
    assert _verify_raw(key, header={**HEADER, "iat": NOW - 269, "exp": NOW + 31})
    with pytest.raises(TokenExpiredError) as expired:
        _verify_raw(key, header={**HEADER, "iat": NOW - 330, "exp": NOW - 30})
    assert (expired.value.status_code, expired.value.error_code) == (
        401,
        "token_expired",
    )
    with pytest.raises(InvalidJwsError, match="maximum"):
        _verify_raw(key, header={**HEADER, "exp": NOW + 301})
    with pytest.raises(InvalidJwsError, match="after"):
        _verify_raw(key, header={**HEADER, "exp": NOW})


@pytest.mark.unit
def test_authentic_signature_is_checked_before_temporal_state(
    key: Ed25519PrivateKey,
) -> None:
    token = _raw_token({**HEADER, "iat": NOW - 400, "exp": NOW - 100}, PAYLOAD, key)
    with pytest.raises(InvalidSignature):
        verify_jws(token, Ed25519PrivateKey.generate().public_key(), policy=POLICY)


@pytest.mark.unit
def test_context_and_signer_guards(key: Ed25519PrivateKey) -> None:
    verified = _verify_raw(key)
    assert (
        require_context(
            verified,
            audience="central-bank",
            resource="POST /escrow/esc-1/release",
            allowed_action=("escrow_release", "escrow_split"),
        )
        is verified
    )
    assert require_signer(verified, expected_kid="a-platform") is verified
    for kwargs in [
        {
            "audience": "court",
            "resource": HEADER["resource"],
            "allowed_action": "escrow_release",
        },
        {
            "audience": "central-bank",
            "resource": "POST /other",
            "allowed_action": "escrow_release",
        },
        {
            "audience": "central-bank",
            "resource": HEADER["resource"],
            "allowed_action": "credit",
        },
    ]:
        with pytest.raises(InvalidPayloadError) as error:
            require_context(verified, **kwargs)  # type: ignore[arg-type]
        assert (error.value.status_code, error.value.error_code) == (
            400,
            "invalid_payload",
        )
    with pytest.raises(ForbiddenError) as forbidden:
        require_signer(verified, expected_kid="a-other")
    assert (forbidden.value.status_code, forbidden.value.error_code) == (
        403,
        "forbidden",
    )


@pytest.mark.unit
def test_platform_signer_requires_positive_ttl_and_explicit_context(
    tmp_path: Any,
) -> None:
    generate_keypair("platform", tmp_path)
    for ttl in [None, 0, -1, True]:
        with pytest.raises(ValueError):
            PlatformSigner("a-platform", str(tmp_path / "platform.key"), ttl)  # type: ignore[arg-type]
    signer = PlatformSigner("a-platform", str(tmp_path / "platform.key"), 300)
    token = signer.sign(
        PAYLOAD,
        audience="central-bank",
        resource="POST /escrow/esc-1/release",
    )
    assert parse_jws(token).header.aud == "central-bank"


@pytest.mark.unit
def test_platform_signer_requires_canonical_validated_keypair(tmp_path: Path) -> None:
    missing = tmp_path / "missing.key"
    with pytest.raises(ValueError):
        PlatformSigner("a-platform", str(missing), 300)

    partial_dir = tmp_path / "partial"
    partial_dir.mkdir()
    generate_keypair("platform", partial_dir)
    (partial_dir / "platform.pub").unlink()
    with pytest.raises(ValueError):
        PlatformSigner("a-platform", str(partial_dir / "platform.key"), 300)

    mismatch_dir = tmp_path / "mismatch"
    mismatch_dir.mkdir()
    generate_keypair("platform", mismatch_dir)
    other = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    (mismatch_dir / "platform.pub").write_bytes(other)
    with pytest.raises(ValueError):
        PlatformSigner("a-platform", str(mismatch_dir / "platform.key"), 300)

    insecure_dir = tmp_path / "insecure"
    insecure_dir.mkdir()
    generate_keypair("platform", insecure_dir)
    (insecure_dir / "platform.key").chmod(0o644)
    with pytest.raises(ValueError):
        PlatformSigner("a-platform", str(insecure_dir / "platform.key"), 300)

    symlink_dir = tmp_path / "symlink"
    symlink_dir.mkdir()
    generate_keypair("target", symlink_dir)
    (symlink_dir / "platform.key").symlink_to(symlink_dir / "target.key")
    (symlink_dir / "platform.pub").symlink_to(symlink_dir / "target.pub")
    (symlink_dir / "platform.lock").symlink_to(symlink_dir / "target.lock")
    with pytest.raises(ValueError):
        PlatformSigner("a-platform", str(symlink_dir / "platform.key"), 300)

    with pytest.raises(ValueError, match="canonical"):
        PlatformSigner("a-platform", str(tmp_path / "not-a-key.pem"), 300)


@pytest.mark.unit
def test_identity_success_parser_binds_complete_wire_to_requested_token(
    key: Ed25519PrivateKey,
) -> None:
    token = _raw_token(HEADER, PAYLOAD, key)
    wire = {
        "valid": True,
        "agent_id": "a-platform",
        "protected_header": HEADER,
        "payload": PAYLOAD,
    }
    assert parse_identity_verification_response(wire, token) == _verify_raw(key)
    for changed in [
        {**wire, "agent_id": "a-other"},
        {**wire, "payload": {**PAYLOAD, "escrow_id": "other"}},
        {**wire, "payload": {**PAYLOAD, "value": float("inf")}},
        {**wire, "payload": {**PAYLOAD, "value": ("not", "json")}},
        {**wire, "extra": 1},
    ]:
        with pytest.raises(UpstreamContractError) as error:
            parse_identity_verification_response(changed, token)
        assert error.value.status_code == 502


@pytest.mark.unit
@pytest.mark.parametrize(
    ("requested", "claimed"),
    [
        ({"action": "x", "value": 1}, {"action": "x", "value": True}),
        ({"action": "x", "value": 1}, {"action": "x", "value": 1.0}),
        (
            {"action": "x", "value": [1, {"nested": False}]},
            {"action": "x", "value": [True, {"nested": 0}]},
        ),
    ],
)
def test_identity_binding_compares_json_types_recursively(
    requested: dict[str, object],
    claimed: dict[str, object],
    key: Ed25519PrivateKey,
) -> None:
    token = _raw_token(HEADER, requested, key)
    wire = {
        "valid": True,
        "agent_id": "a-platform",
        "protected_header": HEADER,
        "payload": claimed,
    }
    with pytest.raises(UpstreamContractError, match="bound"):
        parse_identity_verification_response(wire, token)


@pytest.mark.unit
def test_claimed_valid_identity_wraps_unparseable_requested_token() -> None:
    wire = {
        "valid": True,
        "agent_id": "a-platform",
        "protected_header": HEADER,
        "payload": PAYLOAD,
    }
    with pytest.raises(UpstreamContractError) as error:
        parse_identity_verification_response(wire, "not.a.valid-token")
    assert error.value.status_code == 502


@pytest.mark.unit
def test_claimed_valid_identity_wraps_recursive_wire_shape(
    key: Ed25519PrivateKey,
) -> None:
    token = _raw_token(HEADER, PAYLOAD, key)
    nested: object = None
    for _index in range(1_100):
        nested = [nested]
    wire = {
        "valid": True,
        "agent_id": "a-platform",
        "protected_header": HEADER,
        "payload": {"action": "x", "nested": nested},
    }
    with pytest.raises(UpstreamContractError) as error:
        parse_identity_verification_response(wire, token)
    assert error.value.status_code == 502


@pytest.mark.unit
def test_identity_invalid_signature_wire_is_never_success(
    key: Ed25519PrivateKey,
) -> None:
    token = _raw_token(HEADER, PAYLOAD, key)
    with pytest.raises(InvalidSignature):
        parse_identity_verification_response({"valid": False, "reason": "bad signature"}, token)
    with pytest.raises(UpstreamContractError):
        parse_identity_verification_response({"valid": False}, token)
    with pytest.raises(UpstreamContractError):
        parse_identity_verification_response({"valid": False, "reason": ""}, token)


@pytest.mark.unit
@pytest.mark.parametrize("indent", [None, 2])
def test_verification_preserves_original_json_serialization(indent: int | None) -> None:
    key = Ed25519PrivateKey.generate()
    header_json = json.dumps(dict(reversed(list(HEADER.items()))), indent=indent)
    payload_json = json.dumps(dict(reversed(list(PAYLOAD.items()))), indent=indent)
    token = _raw_token(HEADER, PAYLOAD, key, header_json=header_json, payload_json=payload_json)
    verified = verify_jws(token, key.public_key(), policy=POLICY)
    assert vars(verified.header) == HEADER
    assert verified.payload == PAYLOAD


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{}, {"action": None}, {"action": True}, {"action": ""}])
def test_bad_signature_precedes_action_content_validation(payload: dict[str, object]) -> None:
    expected_key = Ed25519PrivateKey.generate()
    wrong_key = Ed25519PrivateKey.generate()
    token = _raw_token(HEADER, payload, wrong_key)
    with pytest.raises(InvalidSignature):
        verify_jws(token, expected_key.public_key(), policy=POLICY)


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{}, {"action": None}, {"action": True}, {"action": ""}])
def test_unverified_structural_parse_does_not_admit_action(payload: dict[str, object]) -> None:
    key = Ed25519PrivateKey.generate()
    parsed = parse_jws(_raw_token(HEADER, payload, key))
    assert parsed.payload == payload
    assert not isinstance(parsed, VerifiedJws)


@pytest.mark.unit
@pytest.mark.parametrize(
    "issued_at,expires_at,error",
    [
        (NOW + 31, NOW + 91, TokenNotYetValidError),
        (NOW - 100, NOW - 30, TokenExpiredError),
    ],
)
def test_temporal_rejection_precedes_authenticated_action_validation(
    issued_at: int, expires_at: int, error: type[Exception]
) -> None:
    key = Ed25519PrivateKey.generate()
    header = {**HEADER, "iat": issued_at, "exp": expires_at}
    token = _raw_token(header, {}, key)
    with pytest.raises(error):
        verify_jws(token, key.public_key(), policy=POLICY)


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{}, {"action": None}, {"action": True}, {"action": ""}])
def test_issuer_and_identity_success_still_require_valid_action(payload: dict[str, object]) -> None:
    key = Ed25519PrivateKey.generate()
    with pytest.raises(InvalidPayloadError):
        create_jws(
            payload,
            key,
            kid="a-platform",
            audience="central-bank",
            resource="POST /escrow/esc-1/release",
            token_ttl_seconds=300,
        )
    token = _raw_token(HEADER, payload, key)
    with pytest.raises(UpstreamContractError):
        parse_identity_verification_response(
            {
                "valid": True,
                "agent_id": "a-platform",
                "protected_header": HEADER,
                "payload": payload,
            },
            token,
        )


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{}, {"action": None}, {"action": True}, {"action": ""}])
def test_context_boundary_rejects_incomplete_authenticated_payload(
    payload: dict[str, object],
) -> None:
    verified = VerifiedJws(header=JwsProtectedHeader(**HEADER), payload=payload)
    with pytest.raises(InvalidPayloadError):
        require_context(
            verified,
            audience="central-bank",
            resource="POST /escrow/esc-1/release",
            allowed_action="escrow_release",
        )
