"""Unit tests for service_auth Ed25519 signing, JWS, and token expiry."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from service_auth import signing
from service_auth.signing import (
    PlatformSigner,
    TokenExpiredError,
    create_jws,
    generate_keypair,
    load_private_key,
    load_public_key,
    public_key_to_b64,
    verify_jws,
)

if TYPE_CHECKING:
    from pathlib import Path


def _decode_segment(segment: str) -> dict[str, object]:
    padding = "=" * (4 - len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


@pytest.mark.unit
class TestKeygenRoundtrip:
    def test_creates_and_loads_keys(self, tmp_path: Path) -> None:
        original_private, original_public = generate_keypair("alice", tmp_path)
        assert (tmp_path / "alice.key").exists()
        assert (tmp_path / "alice.pub").exists()

        loaded_private = load_private_key(tmp_path / "alice.key")
        loaded_public = load_public_key(tmp_path / "alice.pub")

        assert isinstance(loaded_private, Ed25519PrivateKey)
        assert public_key_to_b64(original_public) == public_key_to_b64(loaded_public)
        assert len(base64.b64decode(public_key_to_b64(original_private.public_key()))) == 32


@pytest.mark.unit
class TestSignVerify:
    def test_sign_and_verify_roundtrip(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        payload = {"action": "escrow_release", "escrow_id": "esc-1", "amount": 50}
        token = create_jws(payload, private_key, kid="a-1")

        result = verify_jws(token, private_key.public_key())
        assert result == payload

    def test_verify_rejects_wrong_key(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        wrong_public = Ed25519PrivateKey.generate().public_key()
        token = create_jws({"action": "test"}, private_key, kid="a-1")

        with pytest.raises(InvalidSignature):
            verify_jws(token, wrong_public)

    def test_malformed_token_raises_value_error(self) -> None:
        public_key = Ed25519PrivateKey.generate().public_key()
        with pytest.raises(ValueError, match="Invalid JWS"):
            verify_jws("only.two", public_key)

    def test_canonicalization_is_key_order_independent(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        token_a = create_jws({"b": 2, "a": 1}, private_key, kid="a-1")
        token_b = create_jws({"a": 1, "b": 2}, private_key, kid="a-1")
        # sort_keys canonicalization makes the two payload orderings identical.
        assert token_a == token_b


@pytest.mark.unit
class TestTamperRejection:
    def test_rejects_tampered_payload(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        token = create_jws({"action": "test", "amount": 1}, private_key, kid="a-1")
        header_b64, payload_b64, sig_b64 = token.split(".")
        tampered = f"{header_b64}.{payload_b64}xy.{sig_b64}"

        with pytest.raises(InvalidSignature):
            verify_jws(tampered, private_key.public_key())

    def test_rejects_tampered_header(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        token = create_jws({"action": "test"}, private_key, kid="a-1")
        _header_b64, payload_b64, sig_b64 = token.split(".")
        # Forge a header claiming a different kid.
        forged = (
            base64.urlsafe_b64encode(
                json.dumps({"alg": "EdDSA", "typ": "JWT", "kid": "a-imposter"}).encode()
            )
            .rstrip(b"=")
            .decode()
        )
        tampered = f"{forged}.{payload_b64}.{sig_b64}"

        with pytest.raises(InvalidSignature):
            verify_jws(tampered, private_key.public_key())


@pytest.mark.unit
class TestExpiryHeaderClaims:
    def test_ttl_stamps_iat_exp_into_header_not_payload(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        payload = {"action": "credit", "amount": 100}
        token = create_jws(payload, private_key, kid="a-1", token_ttl_seconds=300)

        header_b64, payload_b64, _ = token.split(".")
        header = _decode_segment(header_b64)
        decoded_payload = _decode_segment(payload_b64)

        assert header["exp"] == header["iat"] + 300  # type: ignore[operator]
        assert "exp" not in decoded_payload
        assert "iat" not in decoded_payload
        assert decoded_payload == payload

    def test_legacy_token_without_exp_is_accepted(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        token = create_jws({"action": "test"}, private_key, kid="a-1")
        header = _decode_segment(token.split(".")[0])
        assert "exp" not in header

        result = verify_jws(token, private_key.public_key())
        assert result["action"] == "test"

    @pytest.mark.usefixtures("reset_clock")
    def test_expired_token_is_rejected(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        signing._clock = lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        token = create_jws({"action": "test"}, private_key, kid="a-1", token_ttl_seconds=60)

        # Advance the clock past the 60s lifetime.
        signing._clock = lambda: datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
        with pytest.raises(TokenExpiredError):
            verify_jws(token, private_key.public_key())

    @pytest.mark.usefixtures("reset_clock")
    def test_unexpired_token_is_accepted(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        signing._clock = lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        token = create_jws({"action": "test"}, private_key, kid="a-1", token_ttl_seconds=300)

        signing._clock = lambda: datetime(2026, 1, 1, 12, 2, 0, tzinfo=UTC)
        result = verify_jws(token, private_key.public_key())
        assert result["action"] == "test"


@pytest.mark.unit
class TestCrossCanonicalizationCompatibility:
    """The lib's verify must accept tokens from both pre-WP-02 signers."""

    def _public_key(self, fixtures: dict[str, object]) -> Ed25519PublicKey:
        raw = base64.b64decode(str(fixtures["public_key_b64_raw"]))
        return Ed25519PublicKey.from_public_bytes(raw)

    def test_accepts_base_agent_old_canonicalization(
        self, old_signer_tokens: dict[str, object]
    ) -> None:
        public_key = self._public_key(old_signer_tokens)
        token = str(old_signer_tokens["base_agent_old_token"])
        result = verify_jws(token, public_key)
        assert result == old_signer_tokens["payload"]

    def test_accepts_platform_signer_canonicalization(
        self, old_signer_tokens: dict[str, object]
    ) -> None:
        public_key = self._public_key(old_signer_tokens)
        token = str(old_signer_tokens["platform_signer_token"])
        result = verify_jws(token, public_key)
        assert result == old_signer_tokens["payload"]


@pytest.mark.unit
class TestPlatformSigner:
    def test_signs_and_verifies(self, tmp_path: Path) -> None:
        private_key, public_key = generate_keypair("platform", tmp_path)
        signer = PlatformSigner(
            platform_agent_id="a-platform",
            private_key_path=str(tmp_path / "platform.key"),
            token_ttl_seconds=300,
        )
        token = signer.sign({"action": "escrow_release", "escrow_id": "esc-1"})

        header = _decode_segment(token.split(".")[0])
        assert header["kid"] == "a-platform"
        assert "exp" in header
        result = verify_jws(token, public_key)
        assert result["action"] == "escrow_release"
        assert public_key_to_b64(private_key.public_key()) == public_key_to_b64(public_key)

    def test_legacy_signer_omits_exp(self, tmp_path: Path) -> None:
        generate_keypair("platform", tmp_path)
        signer = PlatformSigner(
            platform_agent_id="a-platform",
            private_key_path=str(tmp_path / "platform.key"),
            token_ttl_seconds=None,
        )
        token = signer.sign({"action": "escrow_release", "escrow_id": "esc-1"})
        header = _decode_segment(token.split(".")[0])
        assert "exp" not in header
