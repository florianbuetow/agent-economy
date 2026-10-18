"""Router identity stubs must not disguise programming errors as dependency failures."""

from __future__ import annotations

from json import JSONDecodeError
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from service_commons.exceptions import ServiceError

from tests.unit.routers.conftest import make_fixture_verify_jws

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, TypeError])
async def test_unexpected_fixture_verifier_exception_propagates(
    error_type: type[Exception],
) -> None:
    error = error_type("Injected fixture programming failure")
    state = SimpleNamespace(
        platform_agent=SimpleNamespace(validate_certificate=MagicMock(side_effect=error))
    )
    verify = make_fixture_verify_jws(state)
    with pytest.raises(error_type) as raised:
        await verify("unused-token")
    assert raised.value is error


async def test_malformed_fixture_verifier_return_is_not_dependency_unavailability() -> None:
    state = SimpleNamespace(
        platform_agent=SimpleNamespace(validate_certificate=MagicMock(return_value=None))
    )
    verify = make_fixture_verify_jws(state)
    with pytest.raises(TypeError, match="VerifiedJws"):
        await verify("unused-token")


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("Injected unavailable Identity"),
        TimeoutError("Injected Identity timeout"),
        JSONDecodeError("Injected malformed dependency response", "not-json", 0),
    ],
)
async def test_declared_dependency_failures_remain_typed(error: Exception) -> None:
    state = SimpleNamespace(
        platform_agent=SimpleNamespace(validate_certificate=MagicMock(side_effect=error))
    )
    verify = make_fixture_verify_jws(state)
    with pytest.raises(ServiceError) as raised:
        await verify("unused-token")
    assert raised.value.error == "identity_service_unavailable"
    assert raised.value.status_code == 502
    assert raised.value.__cause__ is error
