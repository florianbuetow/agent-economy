"""GAP-F6/T-045: true multi-connection gateway concurrency.

`test_cross_cutting.py::TestConcurrency::test_concurrent_duplicate_registrations_safe`
(CONC-02) races two *identical* registration requests (same agent_id, same
public_key) through a single in-process `TestClient` — that scenario can
legitimately return two `201`s if the write path treats an exact-duplicate
request as an idempotent replay, and it never leaves the same Python process
(`TestClient` dispatches over `httpx.ASGITransport`, not a real socket).

This file instead races genuinely *different* agent registrations (different
agent_id, same public_key) — a scenario no idempotency check can legitimately
let through twice — each over its own real TCP connection to a live,
network-bound gateway process. Exactly one must win.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pytest


def _agent_payload(agent_id: str, public_key: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": agent_id,
        "public_key": public_key,
        "registered_at": "2026-03-01T09:00:00Z",
        "event": {
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": "2026-03-01T09:00:00Z",
            "agent_id": agent_id,
            "summary": f"{agent_id} registered",
            "payload": "{}",
        },
    }


def _register_over_own_connection(
    gateway_url: str,
    payload: dict[str, object],
    gateway_auth: httpx.Auth,
) -> int:
    """Open a brand-new httpx.Client (its own TCP connection) and register.

    A shared client's connection pool can serialize or reuse a socket across
    "concurrent" calls; a fresh client per call guarantees each request rides
    its own connection, matching what N real, independent callers would do.
    """
    with httpx.Client(base_url=gateway_url, timeout=10.0, auth=gateway_auth) as client:
        response = client.post("/identity/agents", json=payload)
        return response.status_code


@pytest.mark.integration
class TestTrueMultiConnectionConcurrency:
    """T-045: exactly one 201 among concurrently-racing distinct agents
    claiming the same public_key, each over its own live connection."""

    def test_exactly_one_201_among_five_racing_distinct_agents(
        self,
        gateway_url: str,
        gateway_auth: httpx.Auth,
    ) -> None:
        shared_public_key = f"ed25519:{uuid.uuid4()}"
        payloads = [_agent_payload(f"a-conc-{uuid.uuid4()}", shared_public_key) for _ in range(5)]

        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            futures = [
                pool.submit(_register_over_own_connection, gateway_url, payload, gateway_auth)
                for payload in payloads
            ]
            results = [f.result() for f in as_completed(futures)]

        assert sorted(results) == [201, 409, 409, 409, 409], (
            f"expected exactly one 201 and four 409s, got {sorted(results)}"
        )

    def test_winner_is_readable_and_no_duplicate_rows_exist(
        self,
        gateway_url: str,
        gateway_auth: httpx.Auth,
    ) -> None:
        """The single winning agent_id is the one actually persisted; the
        losers never made it into identity_agents at all."""
        shared_public_key = f"ed25519:{uuid.uuid4()}"
        agent_ids = [f"a-conc-{uuid.uuid4()}" for _ in range(5)]
        payloads = [_agent_payload(aid, shared_public_key) for aid in agent_ids]

        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            futures = {
                pool.submit(
                    _register_over_own_connection,
                    gateway_url,
                    payload,
                    gateway_auth,
                ): aid
                for payload, aid in zip(payloads, agent_ids, strict=True)
            }
            winners = [futures[f] for f in as_completed(futures) if f.result() == 201]

        assert len(winners) == 1
        winner_id = winners[0]

        with httpx.Client(base_url=gateway_url, timeout=10.0, auth=gateway_auth) as client:
            resp = client.get(f"/identity/agents/{winner_id}")
            assert resp.status_code == 200
            assert resp.json()["public_key"] == shared_public_key

            for aid in agent_ids:
                if aid == winner_id:
                    continue
                loser_resp = client.get(f"/identity/agents/{aid}")
                assert loser_resp.status_code == 404
