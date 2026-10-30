"""Plan item 0.8 (Court part) and the asset-read contract for DeliverableFetcher.

Judge/provider failures surface only a generic ``Judge N failed`` message; the
provider exception text (which may carry api_base, keys or raw model output)
is logged server-side, never returned. The fetcher authenticates its asset
reads with a platform-signed Bearer token.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from service_commons.exceptions import ServiceError

from court_service.judges.base import DisputeContext
from court_service.judges.llm_judge import LLMJudge
from court_service.services.evidence_bundle import EvidencePolicy, build_evidence_bundle
from court_service.services.ruling_orchestrator import DeliverableFetcher, RulingOrchestrator

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_SECRET = "api_base=http://10.0.0.5:1234 key=sk-live-SECRET raw-model-output"


def _context() -> DisputeContext:
    bundle = build_evidence_bundle(
        {"spec": "spec", "deliverables": []},
        {"claimant_id": "p", "respondent_id": "w", "claim": "claim", "rebuttal": None},
    )
    return DisputeContext(task_title="title", reward=100, evidence=bundle)


async def test_llm_judge_failure_message_is_generic() -> None:
    judge = LLMJudge(
        judge_id="judge-0",
        model="m",
        temperature=0.0,
        api_base="http://10.0.0.5:1234",
        api_key="sk-live-SECRET",
    )
    with (
        patch(
            "court_service.judges.llm_judge.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError(_SECRET)),
        ),
        pytest.raises(ServiceError) as excinfo,
    ):
        await judge.evaluate(_context())

    assert excinfo.value.status_code == 502
    assert excinfo.value.message == "Judge judge-0 failed"
    assert "SECRET" not in str(excinfo.value.details)


async def test_orchestrator_judge_failure_message_is_generic() -> None:
    orchestrator = RulingOrchestrator(
        store=AsyncMock(),
        feedback_extremely_satisfied_cutoff=80,
        feedback_satisfied_cutoff=40,
        feedback_comment_max_length=256,
        judging_lease_seconds=60,
    )
    failing = AsyncMock()
    failing.evaluate = AsyncMock(side_effect=RuntimeError(_SECRET))

    with pytest.raises(ServiceError) as excinfo:
        await orchestrator._evaluate_judges([failing], _context())

    assert excinfo.value.status_code == 502
    assert excinfo.value.message == "Judge 0 failed"


class _Signer:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], str, str]] = []

    def sign(self, payload: dict[str, Any], *, audience: str, resource: str) -> str:
        self.calls.append((payload, audience, resource))
        return f"token-{len(self.calls)}"


async def test_deliverable_fetcher_sends_platform_bearer_tokens() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/tasks/t-1/assets":
            return httpx.Response(
                200,
                json={
                    "assets": [
                        {
                            "asset_id": "as-1",
                            "uploader_id": "a-worker",
                            "filename": "solution.txt",
                            "content_type": "text/plain",
                            "size_bytes": 8,
                            "content_hash": hashlib.sha256(b"solution").hexdigest(),
                            "uploaded_at": "2026-10-30T10:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(200, content=b"solution")

    signer = _Signer()
    fetcher = DeliverableFetcher(
        task_board_url="http://task-board.test",
        policy=EvidencePolicy(
            max_total_bytes=1024,
            max_file_bytes=1024,
            allowed_extensions=frozenset({".txt"}),
            allowed_content_types=frozenset({"text/plain"}),
        ),
        timeout_seconds=5,
        signer=signer,
    )
    fetcher._client = httpx.AsyncClient(
        base_url="http://task-board.test", transport=httpx.MockTransport(handler)
    )

    files = await fetcher.fetch("t-1")
    await fetcher.close()

    assert [(f.status, f.text) for f in files] == [("included", "solution")]
    assert [r.headers["Authorization"] for r in seen] == ["Bearer token-1", "Bearer token-2"]
    assert signer.calls == [
        ({"action": "list_assets", "task_id": "t-1"}, "task-board", "GET /tasks/t-1/assets"),
        (
            {"action": "download_asset", "task_id": "t-1", "asset_id": "as-1"},
            "task-board",
            "GET /tasks/t-1/assets/as-1",
        ),
    ]
