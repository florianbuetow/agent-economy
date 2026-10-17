"""Strict compact Ed25519 JWS profile for service authorization."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidSignature

if TYPE_CHECKING:
    from collections.abc import Callable

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

_HEADER_FIELDS = {"alg", "typ", "kid", "iat", "exp", "aud", "resource"}
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_RESOURCE = re.compile(r"^[A-Z]+ /[^\s?#{}]*$")


class JwsAdmissionError(ValueError):
    """Base error with stable HTTP admission mapping."""

    status_code: int
    error_code: str

    def __init__(self, message: str) -> None:
        super().__init__(message)


class InvalidJwsError(JwsAdmissionError):
    """The compact token does not satisfy the strict JWS profile."""

    status_code = 400
    error_code = "invalid_jws"


class InvalidPayloadError(JwsAdmissionError):
    """Authenticated token context does not match receiver-owned context."""

    status_code = 400
    error_code = "invalid_payload"


class ForbiddenError(JwsAdmissionError):
    """Authenticated signer is not the required local principal."""

    status_code = 403
    error_code = "forbidden"


class TokenExpiredError(JwsAdmissionError):
    """The authentic token is expired under the receiver's policy."""

    status_code = 401
    error_code = "token_expired"


class TokenNotYetValidError(JwsAdmissionError):
    """The authentic token was issued too far in the future."""

    status_code = 401
    error_code = "token_not_yet_valid"


class UpstreamContractError(JwsAdmissionError):
    """Identity returned a malformed or unbound successful verification."""

    status_code = 502
    error_code = "invalid_upstream_response"


@dataclass(frozen=True)
class JwsProtectedHeader:
    """The exact protected header accepted by the MP-03 profile."""

    alg: str
    typ: str
    kid: str
    iat: int
    exp: int
    aud: str
    resource: str


@dataclass(frozen=True)
class TokenPolicy:
    """Receiver-owned temporal admission limits."""

    max_lifetime_seconds: int
    clock_skew_seconds: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_lifetime_seconds, bool)
            or not isinstance(self.max_lifetime_seconds, int)
            or self.max_lifetime_seconds <= 0
        ):
            msg = "max_lifetime_seconds must be a positive integer"
            raise ValueError(msg)
        if (
            isinstance(self.clock_skew_seconds, bool)
            or not isinstance(self.clock_skew_seconds, int)
            or self.clock_skew_seconds < 0
        ):
            msg = "clock_skew_seconds must be a nonnegative integer"
            raise ValueError(msg)


@dataclass(frozen=True)
class ParsedJws:
    """Strictly decoded compact JWS prior to cryptographic verification."""

    header: JwsProtectedHeader
    payload: dict[str, object]
    signing_input: bytes
    signature: bytes


@dataclass(frozen=True)
class VerifiedJws:
    """Cryptographically verified protected header and business payload."""

    header: JwsProtectedHeader
    payload: dict[str, object]


def _system_clock() -> datetime:
    return datetime.now(UTC)


_clock: Callable[[], datetime] = _system_clock


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str, *, segment: str) -> bytes:
    if not value or _BASE64URL.fullmatch(value) is None:
        msg = f"Invalid JWS {segment}: expected unpadded base64url"
        raise InvalidJwsError(msg)
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        msg = f"Invalid JWS {segment}: malformed base64url"
        raise InvalidJwsError(msg) from exc


def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            msg = f"Invalid JWS JSON: duplicate key {key!r}"
            raise InvalidJwsError(msg)
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    msg = f"Invalid JWS JSON: non-finite number {value!r}"
    raise InvalidJwsError(msg)


def _decode_json_object(value: bytes, *, segment: str) -> dict[str, object]:
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_reject_duplicate,
            parse_constant=_reject_constant,
        )
        _validate_json_value(decoded)
    except InvalidJwsError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        msg = f"Invalid JWS {segment}: malformed JSON"
        raise InvalidJwsError(msg) from exc
    if not isinstance(decoded, dict):
        msg = f"Invalid JWS {segment}: expected a JSON object"
        raise InvalidJwsError(msg)
    return decoded


def _validate_json_value(value: object) -> None:
    """Reject Python values that cannot preserve strict finite JSON semantics."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = "Invalid JWS JSON: expected finite numbers"
            raise InvalidJwsError(msg)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                msg = "Invalid JWS JSON: object keys must be strings"
                raise InvalidJwsError(msg)
            _validate_json_value(item)
        return
    msg = "Invalid JWS JSON: unsupported value type"
    raise InvalidJwsError(msg)


def _nonblank_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        msg = f"Invalid JWS protected header {field}: expected a non-blank string"
        raise InvalidJwsError(msg)
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        msg = f"Invalid JWS protected header {field}: control characters are forbidden"
        raise InvalidJwsError(msg)
    return value


def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"Invalid JWS protected header {field}: expected an integer"
        raise InvalidJwsError(msg)
    return value


def _parse_header(raw: dict[str, object]) -> JwsProtectedHeader:
    if set(raw) != _HEADER_FIELDS:
        msg = "Invalid JWS protected header: expected exactly the required profile fields"
        raise InvalidJwsError(msg)
    alg = raw["alg"]
    typ = raw["typ"]
    if alg != "EdDSA" or not isinstance(alg, str):
        msg = "Invalid JWS protected header alg: expected 'EdDSA'"
        raise InvalidJwsError(msg)
    if typ != "JWT" or not isinstance(typ, str):
        msg = "Invalid JWS protected header typ: expected 'JWT'"
        raise InvalidJwsError(msg)
    kid = _nonblank_string(raw["kid"], field="kid")
    audience = _nonblank_string(raw["aud"], field="aud")
    resource = _nonblank_string(raw["resource"], field="resource")
    if _RESOURCE.fullmatch(resource) is None or "://" in resource:
        msg = "Invalid JWS protected header resource: expected 'METHOD /concrete/path'"
        raise InvalidJwsError(msg)
    issued_at = _integer(raw["iat"], field="iat")
    expires_at = _integer(raw["exp"], field="exp")
    if expires_at <= issued_at:
        msg = "Invalid JWS protected header exp: must be after iat"
        raise InvalidJwsError(msg)
    return JwsProtectedHeader(
        alg=alg,
        typ=typ,
        kid=kid,
        iat=issued_at,
        exp=expires_at,
        aud=audience,
        resource=resource,
    )


def _parse_payload(raw: dict[str, object]) -> dict[str, object]:
    try:
        _validate_json_value(raw)
    except RecursionError as exc:
        msg = "Invalid JWS payload: recursive or excessively nested JSON"
        raise InvalidJwsError(msg) from exc
    action = raw.get("action")
    if not isinstance(action, str) or not action.strip() or action != action.strip():
        msg = "Invalid JWS payload action: expected a non-blank string"
        raise InvalidPayloadError(msg)
    return raw


def parse_jws(token: str) -> ParsedJws:
    """Parse the strict wire structure without authenticating or admitting an action."""
    if not isinstance(token, str):
        msg = "Invalid JWS format: token must be a string"
        raise InvalidJwsError(msg)
    parts = token.split(".")
    if len(parts) != 3:
        msg = "Invalid JWS format: expected 3 dot-separated parts"
        raise InvalidJwsError(msg)
    header_segment, payload_segment, signature_segment = parts
    header_raw = _decode_json_object(
        _b64url_decode(header_segment, segment="protected header"),
        segment="protected header",
    )
    payload_raw = _decode_json_object(
        _b64url_decode(payload_segment, segment="payload"),
        segment="payload",
    )
    signature = _b64url_decode(signature_segment, segment="signature")
    if len(signature) != 64:
        msg = "Invalid JWS signature: Ed25519 signatures must be exactly 64 bytes"
        raise InvalidJwsError(msg)
    return ParsedJws(
        header=_parse_header(header_raw),
        payload=payload_raw,
        signing_input=f"{header_segment}.{payload_segment}".encode("ascii"),
        signature=signature,
    )


def _canonical(value: dict[str, object]) -> bytes:
    try:
        _validate_json_value(value)
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        msg = "Invalid JWS payload: expected finite JSON-compatible values"
        raise InvalidJwsError(msg) from exc


def create_jws(
    payload: dict[str, object],
    private_key: Ed25519PrivateKey,
    *,
    kid: str,
    audience: str,
    resource: str,
    token_ttl_seconds: int,
) -> str:
    """Create one strict compact Ed25519 token with explicit context and lifetime."""
    if (
        isinstance(token_ttl_seconds, bool)
        or not isinstance(token_ttl_seconds, int)
        or token_ttl_seconds <= 0
    ):
        msg = "token_ttl_seconds must be a positive integer"
        raise ValueError(msg)
    issued_at = int(_clock().timestamp())
    header = _parse_header(
        {
            "alg": "EdDSA",
            "typ": "JWT",
            "kid": kid,
            "iat": issued_at,
            "exp": issued_at + token_ttl_seconds,
            "aud": audience,
            "resource": resource,
        }
    )
    payload = _parse_payload(payload)
    header_segment = _b64url_encode(_canonical(vars(header)))
    payload_segment = _b64url_encode(_canonical(payload))
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    return f"{header_segment}.{payload_segment}.{_b64url_encode(private_key.sign(signing_input))}"


def _authenticate_jws(
    token: str,
    public_key: Ed25519PublicKey,
    *,
    policy: TokenPolicy,
) -> ParsedJws:
    """Verify original compact bytes, then apply explicit receiver time policy."""
    parsed = parse_jws(token)
    public_key.verify(parsed.signature, parsed.signing_input)
    lifetime = parsed.header.exp - parsed.header.iat
    if lifetime > policy.max_lifetime_seconds:
        msg = "Invalid JWS protected header lifetime exceeds policy maximum"
        raise InvalidJwsError(msg)
    now = int(_clock().timestamp())
    if parsed.header.iat > now + policy.clock_skew_seconds:
        msg = "JWS token is not yet valid"
        raise TokenNotYetValidError(msg)
    if now >= parsed.header.exp + policy.clock_skew_seconds:
        msg = "JWS token has expired"
        raise TokenExpiredError(msg)
    return parsed


def verify_jws(
    token: str,
    public_key: Ed25519PublicKey,
    *,
    policy: TokenPolicy,
) -> VerifiedJws:
    """Authenticate a token and validate its action before returning verified data."""
    parsed = _authenticate_jws(token, public_key, policy=policy)
    return VerifiedJws(header=parsed.header, payload=_parse_payload(parsed.payload))


def verify_jws_for_signer(
    token: str,
    public_key: Ed25519PublicKey,
    *,
    policy: TokenPolicy,
    expected_kid: str,
) -> VerifiedJws:
    """Authenticate, bind the local principal, then validate the payload action."""
    parsed = _authenticate_jws(token, public_key, policy=policy)
    if parsed.header.kid != expected_kid:
        msg = "Authenticated token signer is not the required local principal"
        raise ForbiddenError(msg)
    return VerifiedJws(header=parsed.header, payload=_parse_payload(parsed.payload))


def require_context(
    verified: VerifiedJws,
    *,
    audience: str,
    resource: str,
    allowed_action: str | tuple[str, ...],
) -> VerifiedJws:
    """Bind authenticated metadata and action to receiver-owned route context."""
    actions = (allowed_action,) if isinstance(allowed_action, str) else allowed_action
    if (
        verified.header.aud != audience
        or verified.header.resource != resource
        or verified.payload.get("action") not in actions
    ):
        msg = "Authenticated token context does not match the requested operation"
        raise InvalidPayloadError(msg)
    return verified


def require_signer(verified: VerifiedJws, *, expected_kid: str) -> VerifiedJws:
    """Require the authenticated key ID to equal one local principal."""
    if verified.header.kid != expected_kid:
        msg = "Authenticated token signer is not the required local principal"
        raise ForbiddenError(msg)
    return verified


def _wire_header(value: object) -> JwsProtectedHeader:
    if not isinstance(value, dict):
        msg = "Identity success protected_header must be an object"
        raise UpstreamContractError(msg)
    try:
        return _parse_header(value)
    except InvalidJwsError as exc:
        msg = "Identity success protected_header violates the JWS profile"
        raise UpstreamContractError(msg) from exc


def _wire_payload(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = "Identity success payload must be an object"
        raise UpstreamContractError(msg)
    try:
        return _parse_payload(value)
    except (JwsAdmissionError, RecursionError) as exc:
        msg = "Identity success payload violates the JWS profile"
        raise UpstreamContractError(msg) from exc


def _json_values_equal(left: object, right: object) -> bool:
    """Compare validated JSON values without Python's bool/int coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _json_values_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return bool(left == right)


def parse_identity_verification_response(response: object, token: str) -> VerifiedJws:
    """Validate and bind Identity's verification wire result to the requested token."""
    if not isinstance(response, dict) or not isinstance(response.get("valid"), bool):
        msg = "Identity verification response must be an object with boolean valid"
        raise UpstreamContractError(msg)
    if response["valid"] is False:
        if (
            set(response) != {"valid", "reason"}
            or not isinstance(response["reason"], str)
            or not response["reason"].strip()
        ):
            msg = "Identity invalid verification response violates its wire contract"
            raise UpstreamContractError(msg)
        raise InvalidSignature(response["reason"])
    if set(response) != {"valid", "agent_id", "protected_header", "payload"}:
        msg = "Identity success response must contain exactly the required fields"
        raise UpstreamContractError(msg)
    agent_id = response["agent_id"]
    if not isinstance(agent_id, str) or not agent_id.strip():
        msg = "Identity success agent_id must be a non-blank string"
        raise UpstreamContractError(msg)
    header = _wire_header(response["protected_header"])
    payload = _wire_payload(response["payload"])
    try:
        requested = parse_jws(token)
    except JwsAdmissionError as exc:
        msg = "Identity claimed success for an invalid requested token"
        raise UpstreamContractError(msg) from exc
    if (
        agent_id != header.kid
        or header != requested.header
        or not _json_values_equal(payload, requested.payload)
    ):
        msg = "Identity success response is not bound to the requested token"
        raise UpstreamContractError(msg)
    return VerifiedJws(header=header, payload=payload)
