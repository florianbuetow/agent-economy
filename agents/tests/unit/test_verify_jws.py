"""Unit tests for local JWS verification."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.signing import create_jws, verify_jws


@pytest.mark.unit
class TestVerifyJws:
    """Tests for verify_jws."""

    def test_verify_valid_token(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        payload = {"action": "test", "value": 42}
        token = create_jws(payload, private_key, kid="a-123")

        result = verify_jws(token, public_key)

        assert result["action"] == "test"
        assert result["value"] == 42

    def test_verify_rejects_wrong_key(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        wrong_key = Ed25519PrivateKey.generate().public_key()
        token = create_jws({"action": "test"}, private_key)

        with pytest.raises(InvalidSignature):
            verify_jws(token, wrong_key)

    def test_verify_rejects_tampered_payload(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        token = create_jws({"action": "test"}, private_key)

        parts = token.split(".")
        parts[1] = parts[1] + "x"
        tampered = ".".join(parts)

        with pytest.raises(InvalidSignature):
            verify_jws(tampered, public_key)

    def test_verify_rejects_malformed_token(self) -> None:
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        with pytest.raises(ValueError, match="Invalid JWS"):
            verify_jws("not.a.valid.token.here", public_key)

        with pytest.raises(ValueError, match="Invalid JWS"):
            verify_jws("onlyonepart", public_key)
