"""GAP-E7 (second half): /rebuttal must go through RequestValidationMiddleware.

/rebuttal was missing from core/middleware.py's _JSON_VALIDATION_ENDPOINTS, so
unlike every sibling JSON POST endpoint it never got the Content-Type (415) or
body-size (413) checks. Verified against the pre-fix code: malformed JSON to
/rebuttal already cleanly 400s today (parse_json_body's own guard, independent
of this middleware) — that specific claim didn't reproduce. What *is* real and
verified missing: wrong Content-Type doesn't 415, and an oversized body
doesn't 413, both unlike /dispute (its sibling endpoint on the same resource).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.unit.routers.conftest import setup_task_in_dispute

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
class TestRebuttalMiddlewareCoverage:
    async def test_wrong_content_type_returns_415(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ) -> None:
        """/rebuttal rejects a non-JSON Content-Type before touching the token,
        exactly like /dispute (test_content_type_before_token's sibling)."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        resp = await client.post(
            f"/tasks/{task_id}/rebuttal",
            content=b'{"token": "invalid"}',
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415
        assert resp.json()["error"] == "unsupported_media_type"

    async def test_oversized_body_returns_413(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ) -> None:
        """/rebuttal rejects an oversized body before touching the token.

        max_body_size is 1048576 (1 MB) in conftest's test config, matching
        test_security.py::test_body_size_before_token for /tasks.
        """
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        oversized_body = b'{"token": "' + b"x" * (1048576 + 1) + b'"}'
        resp = await client.post(
            f"/tasks/{task_id}/rebuttal",
            content=oversized_body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "payload_too_large"

    async def test_malformed_json_still_returns_clean_400(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ) -> None:
        """Malformed JSON with the correct Content-Type still 400s cleanly, not 500."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        resp = await client.post(
            f"/tasks/{task_id}/rebuttal",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_json"
