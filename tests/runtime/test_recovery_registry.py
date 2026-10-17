"""Recovery records exact public-key identities from a stable registry view."""

import base64

import httpx
import pytest
from scripts.recovery_registry import RegistrySnapshotError, capture_registry_bindings

pytestmark = pytest.mark.integration


def _key(number: int) -> str:
    return "ed25519:" + base64.b64encode(bytes([number]) * 32).decode("ascii")


def test_registry_uses_detail_public_keys_and_ignores_duplicate_names() -> None:
    requested = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/agents":
            return httpx.Response(
                200,
                json={
                    "agents": [
                        {"agent_id": "agent-2", "name": "UI Operator"},
                        {"agent_id": "agent-1", "name": "UI Operator"},
                    ]
                },
            )
        agent_id = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(
            200,
            json={
                "agent_id": agent_id,
                "name": "UI Operator",
                "public_key": _key(int(agent_id[-1])),
            },
        )

    with httpx.Client(
        base_url="http://registry.test", transport=httpx.MockTransport(respond)
    ) as client:
        result = capture_registry_bindings(client, max_attempts=2)
    assert result == {_key(1): "agent-1", _key(2): "agent-2"}
    assert requested == ["/agents", "/agents/agent-1", "/agents/agent-2", "/agents"]


def test_registry_growth_retries_the_entire_list_detail_list_capture() -> None:
    lists = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal lists
        if request.url.path == "/agents":
            lists += 1
            ids = ["agent-1"] if lists == 1 else ["agent-1", "agent-2"]
            return httpx.Response(200, json={"agents": [{"agent_id": item} for item in ids]})
        agent_id = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(
            200, json={"agent_id": agent_id, "public_key": _key(int(agent_id[-1]))}
        )

    with httpx.Client(
        base_url="http://registry.test", transport=httpx.MockTransport(respond)
    ) as client:
        result = capture_registry_bindings(client, max_attempts=2)
    assert result == {_key(1): "agent-1", _key(2): "agent-2"}
    assert lists == 4


def test_continually_changing_registry_fails_at_explicit_attempt_limit() -> None:
    lists = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal lists
        if request.url.path == "/agents":
            lists += 1
            return httpx.Response(200, json={"agents": [{"agent_id": f"agent-{lists}"}]})
        agent_id = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(
            200, json={"agent_id": agent_id, "public_key": _key(int(agent_id[-1]))}
        )

    with (
        httpx.Client(
            base_url="http://registry.test", transport=httpx.MockTransport(respond)
        ) as client,
        pytest.raises(RegistrySnapshotError) as exc,
    ):
        capture_registry_bindings(client, max_attempts=2)
    assert exc.value.reason == "registry_changed_during_capture"
    assert lists == 4


@pytest.mark.parametrize(
    "failure", ["duplicate_id", "duplicate_key", "different_id", "bad_key", "missing_key"]
)
def test_malformed_or_contradictory_registry_never_produces_identity_evidence(failure: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/agents":
            second = "agent-1" if failure == "duplicate_id" else "agent-2"
            return httpx.Response(
                200, json={"agents": [{"agent_id": "agent-1"}, {"agent_id": second}]}
            )
        agent_id = request.url.path.rsplit("/", 1)[1]
        detail = {"agent_id": agent_id, "public_key": _key(int(agent_id[-1]))}
        if failure == "duplicate_key":
            detail["public_key"] = _key(1)
        elif failure == "different_id":
            detail["agent_id"] = "someone-else"
        elif failure == "bad_key":
            detail["public_key"] = "DO-NOT-ECHO-INVALID-KEY"
        elif failure == "missing_key":
            del detail["public_key"]
        return httpx.Response(200, json=detail)

    with (
        httpx.Client(
            base_url="http://registry.test", transport=httpx.MockTransport(respond)
        ) as client,
        pytest.raises(RegistrySnapshotError) as exc,
    ):
        capture_registry_bindings(client, max_attempts=2)
    assert exc.value.reason == "invalid_registry_response"
    assert "DO-NOT-ECHO" not in str(exc.value)


def test_unavailable_registry_is_not_treated_as_an_empty_cold_registry() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(503, text="DO-NOT-ECHO-BODY"))
    with (
        httpx.Client(base_url="http://registry.test", transport=transport) as client,
        pytest.raises(RegistrySnapshotError) as exc,
    ):
        capture_registry_bindings(client, max_attempts=2)
    assert exc.value.reason == "registry_unavailable"
    assert "DO-NOT-ECHO" not in str(exc.value)


def test_empty_registry_is_captured_only_after_two_successful_reads() -> None:
    requested = []

    def respond(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, json={"agents": []})

    with httpx.Client(
        base_url="http://registry.test", transport=httpx.MockTransport(respond)
    ) as client:
        assert capture_registry_bindings(client, max_attempts=1) == {}
    assert requested == ["/agents", "/agents"]
