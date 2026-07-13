"""Root conftest for cross-service integration tests."""

from __future__ import annotations

import socket
import sys
from collections.abc import Generator

import pytest


# On macOS, 'localhost' resolves to ::1 (IPv6) first. Connecting to an unused
# port on ::1 does NOT get an immediate TCP RST — the SYN times out — so httpx
# raises ConnectTimeout instead of the ConnectError the tests expect.
#
# anyio encodes the hostname to bytes before calling socket.getaddrinfo, so we
# must check for both the str and bytes forms of "localhost".
#
# Apply the IPv4 preference at import time so it is in effect for every test.
if sys.platform == "darwin":
    _orig_getaddrinfo = socket.getaddrinfo

    def _ipv4_localhost(
        host: str | bytes | None,
        port: str | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[str, int] | tuple[str, int, int, int]]]:
        if host in ("localhost", b"localhost") and family in (0, socket.AF_UNSPEC):
            family = socket.AF_INET
        return _orig_getaddrinfo(host, port, family, type, proto, flags)  # type: ignore[return-value]

    socket.getaddrinfo = _ipv4_localhost  # type: ignore[assignment]

    @pytest.fixture(autouse=True, scope="session")
    def _restore_getaddrinfo() -> Generator[None, None, None]:
        yield
        socket.getaddrinfo = _orig_getaddrinfo
