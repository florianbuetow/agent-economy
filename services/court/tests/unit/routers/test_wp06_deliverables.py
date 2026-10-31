"""WP-06.4 (GAP-A9): judges see the uploaded deliverable content.

Task Board's get_task carries no deliverable bytes, so the ruling path fetches the
task's assets and feeds their text to the judge panel. This asserts the fetched
solution text reaches the judge's DisputeContext (hence the judge prompt).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from court_service.core.state import get_app_state
from court_service.services.evidence_bundle import EvidenceFile
from tests.helpers import make_mock_judge
from tests.unit.routers.conftest import (
    PLATFORM_AGENT_ID,
    file_and_rebut,
    inject_identity_verify,
    ruling_payload,
    token_body,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


class _FakeDeliverableFetcher:
    def __init__(self, texts: list[str], uploader_id: str) -> None:
        self._files = [
            EvidenceFile(
                asset_id=f"asset-{index}",
                filename=f"solution-{index}.py",
                content_type="text/x-python",
                size_bytes=len(text.encode()),
                sha256=hashlib.sha256(text.encode()).hexdigest(),
                uploader_id=uploader_id,
                uploaded_at="2026-10-30T10:00:00Z",
                status="included",
                text=text,
            )
            for index, text in enumerate(texts)
        ]
        self.calls: list[str] = []

    async def fetch(self, task_id: str) -> list[EvidenceFile]:
        self.calls.append(task_id)
        return list(self._files)


@pytest.mark.unit
async def test_judge_context_contains_uploaded_deliverable(client: AsyncClient) -> None:
    """The fetched deliverable text must appear in the judge's DisputeContext."""
    dispute = await file_and_rebut(client)
    dispute_id = dispute["dispute_id"]

    solution = "SOLUTION-a91f: def add(a, b):\n    return a + b\n"
    capturing_judge = make_mock_judge(worker_pct=70)
    state = get_app_state()
    state.judges = [capturing_judge]
    state.deliverable_fetcher = _FakeDeliverableFetcher([solution], str(dispute["respondent_id"]))

    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
    response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay))
    assert response.status_code == 200

    assert capturing_judge.evaluate.await_count == 1
    context = capturing_judge.evaluate.call_args.args[0]
    assert [f.text for f in context.evidence.outputs] == [solution]
    assert context.evidence.inputs == ()
