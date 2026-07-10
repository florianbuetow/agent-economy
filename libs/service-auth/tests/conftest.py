"""Shared fixtures for service_auth tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from service_auth import signing

if TYPE_CHECKING:
    from collections.abc import Iterator

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def old_signer_tokens() -> dict[str, object]:
    """Tokens captured from the pre-WP-02 signers (base_agent + platform_signer)."""
    return json.loads((_FIXTURES / "old_signer_tokens.json").read_text())


@pytest.fixture()
def reset_clock() -> Iterator[None]:
    """Restore the signing module clock seam after a test overrides it."""
    original = signing._clock
    try:
        yield
    finally:
        signing._clock = original
