"""Plan item 3.2 (G09): versioned judge output with independent quality findings.

Each judge returns ``schema_version``, ``worker_pct``, ``spec_quality``,
``delivery_quality`` and explanations. A malformed or incomplete vote fails the
whole panel with a generic error (never a default of 50). The persisted ruling
carries both quality findings independently of the payout, and Court feedback
uses them instead of deriving ratings from ``worker_pct``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from service_commons.exceptions import ServiceError

from court_service.judges.base import DisputeContext, MockJudge
from court_service.judges.llm_judge import LLMJudge
from court_service.judges.output import parse_judge_output
from court_service.services.evidence_bundle import build_evidence_bundle
from court_service.services.ruling_orchestrator import RulingOrchestrator
from tests.fakes.in_memory_dispute_store import InMemoryDisputeStore
from tests.helpers import make_ruling_acknowledgment

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _valid() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "worker_pct": 30,
        "spec_quality": "extremely_satisfied",
        "spec_quality_explanation": "The spec states exact inputs and outputs.",
        "delivery_quality": "dissatisfied",
        "delivery_quality_explanation": "The output is wrong.",
        "reasoning": "Clear spec, wrong delivery.",
    }


def test_valid_output_parses_all_fields() -> None:
    findings = parse_judge_output(_valid())
    assert findings.worker_pct == 30
    assert findings.spec_quality == "extremely_satisfied"
    assert findings.delivery_quality == "dissatisfied"
    assert findings.spec_quality_explanation.startswith("The spec")
    assert findings.delivery_quality_explanation == "The output is wrong."
    assert findings.reasoning == "Clear spec, wrong delivery."


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("schema_version", None),
        ("worker_pct", 101),
        ("worker_pct", -1),
        ("worker_pct", True),
        ("worker_pct", 50.0),
        ("worker_pct", "50"),
        ("spec_quality", "great"),
        ("delivery_quality", None),
        ("spec_quality_explanation", " "),
        ("delivery_quality_explanation", 3),
        ("reasoning", ""),
    ],
)
def test_malformed_field_is_rejected(field: str, value: object) -> None:
    raw = _valid()
    raw[field] = value
    with pytest.raises(ValueError, match=field):
        parse_judge_output(raw)


@pytest.mark.parametrize("missing", sorted(_valid()))
def test_missing_field_is_rejected(missing: str) -> None:
    raw = _valid()
    del raw[missing]
    with pytest.raises(ValueError, match=missing):
        parse_judge_output(raw)


def _context() -> DisputeContext:
    bundle = build_evidence_bundle(
        {"spec": "Compute 2+2.", "deliverables": []},
        {"claimant_id": "p", "respondent_id": "w", "claim": "c", "rebuttal": None},
    )
    return DisputeContext(task_title="Sum", reward=100, evidence=bundle)


async def test_llm_judge_returns_both_quality_findings() -> None:
    reply = {"choices": [{"message": {"content": json.dumps(_valid())}}]}
    judge = LLMJudge(judge_id="judge-0", model="m", temperature=0.0, api_base=None, api_key=None)
    with patch(
        "court_service.judges.llm_judge.litellm.acompletion", new=AsyncMock(return_value=reply)
    ):
        vote = await judge.evaluate(_context())

    assert vote.worker_pct == 30
    assert vote.spec_quality == "extremely_satisfied"
    assert vote.delivery_quality == "dissatisfied"


async def test_llm_judge_legacy_payout_only_reply_fails() -> None:
    reply = {"choices": [{"message": {"content": '{"worker_pct": 70, "reasoning": "ok"}'}}]}
    judge = LLMJudge(judge_id="judge-0", model="m", temperature=0.0, api_base=None, api_key=None)
    with (
        patch(
            "court_service.judges.llm_judge.litellm.acompletion",
            new=AsyncMock(return_value=reply),
        ),
        pytest.raises(ServiceError) as excinfo,
    ):
        await judge.evaluate(_context())
    assert excinfo.value.error == "judge_unavailable"
    assert excinfo.value.message == "Judge judge-0 failed"


def _orchestrator(store: object) -> RulingOrchestrator:
    return RulingOrchestrator(
        store=store,  # type: ignore[arg-type]
        feedback_extremely_satisfied_cutoff=80,
        feedback_satisfied_cutoff=40,
        feedback_comment_max_length=256,
        judging_lease_seconds=60,
    )


async def test_malformed_vote_fails_the_panel_without_default() -> None:
    good = MockJudge(
        judge_id="judge-0",
        fixed_worker_pct=70,
        spec_quality="satisfied",
        delivery_quality="satisfied",
        reasoning="fine",
    )
    malformed = MagicMock()
    malformed.evaluate = AsyncMock(return_value={"judge_id": "judge-1", "worker_pct": 70})

    with pytest.raises(ServiceError) as excinfo:
        await _orchestrator(MagicMock())._evaluate_judges([good, malformed, good], _context())

    assert excinfo.value.error == "judge_unavailable"
    assert excinfo.value.status_code == 502
    assert excinfo.value.message == "Judge 1 failed"


def _platform() -> MagicMock:
    platform = MagicMock()
    platform.agent_id = "a-platform"

    async def _record_ruling(_task_id: str, payload: dict[str, object]) -> dict[str, object]:
        return make_ruling_acknowledgment(
            payload,
            poster_id="agent-poster",
            worker_id="agent-worker",
            escrow_id="escrow-1",
            reward=100,
            event_id=1,
        )

    platform.record_ruling = AsyncMock(side_effect=_record_ruling)
    platform.submit_platform_feedback = AsyncMock(return_value={"status": "ok"})
    return platform


def _judge(pct: int, spec: str, delivery: str) -> MockJudge:
    return MockJudge(
        judge_id=f"judge-{pct}",
        fixed_worker_pct=pct,
        spec_quality=spec,
        delivery_quality=delivery,
        reasoning=f"vote {pct}",
    )


async def test_ruling_stores_quality_findings_independent_of_payout(tmp_path: Path) -> None:
    store = InMemoryDisputeStore(db_path=str(tmp_path / "court.db"))
    dispute = store.insert_dispute(
        task_id="task-1",
        claimant_id="agent-poster",
        respondent_id="agent-worker",
        claim="Wrong answer",
        escrow_id="escrow-1",
        rebuttal_deadline=datetime.now(UTC).isoformat(),
    )
    dispute_id = str(dispute["dispute_id"])
    store.update_rebuttal(dispute_id, "Right answer")
    platform = _platform()

    # A high payout with a poor spec finding: the old cutoff mapping would have
    # derived spec_quality "dissatisfied" and delivery "extremely_satisfied"
    # from worker_pct 90; the judges' independent findings must win instead.
    await _orchestrator(store).execute_ruling(
        dispute_id=dispute_id,
        judges=[
            _judge(90, "satisfied", "dissatisfied"),
            _judge(95, "extremely_satisfied", "satisfied"),
            _judge(10, "satisfied", "dissatisfied"),
        ],
        task_data={
            "spec": "s",
            "deliverables": [],
            "title": "T",
            "reward": 100,
            "lifecycle_version": 3,
        },
        platform_agent=platform,
    )

    decision = store._state.decisions[dispute_id]
    assert decision["worker_pct"] == 90
    assert decision["request"]["spec_quality"] == "satisfied"
    assert decision["request"]["delivery_quality"] == "dissatisfied"
    assert [v["spec_quality"] for v in decision["votes"]] == [
        "satisfied",
        "extremely_satisfied",
        "satisfied",
    ]
    assert all("delivery_quality_explanation" in v for v in decision["votes"])

    ratings = {
        call.args[0]["category"]: call.args[0]["rating"]
        for call in platform.submit_platform_feedback.await_args_list
    }
    assert ratings == {"spec_quality": "satisfied", "delivery_quality": "dissatisfied"}
    store.close()


def test_mock_judge_rejects_unknown_rating() -> None:
    with pytest.raises(ValueError, match="spec_quality"):
        MockJudge(
            judge_id="judge-0",
            fixed_worker_pct=50,
            spec_quality="ok",
            delivery_quality="satisfied",
            reasoning="r",
        )
