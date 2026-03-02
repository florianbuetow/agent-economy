"""Ruling orchestration for dispute evaluation side effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from service_commons.exceptions import ServiceError

from court_service.judges import DisputeContext, JudgeVote

if TYPE_CHECKING:
    from base_agent.platform import PlatformAgent

    from court_service.judges.base import Judge
    from court_service.services.dispute_store import DisputeStore


class RulingOrchestrator:
    """Orchestrates judge evaluation and ruling side effects."""

    def __init__(self, store: DisputeStore) -> None:
        self._store = store

    @staticmethod
    def _normalize_deliverables(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        return []

    @staticmethod
    def _normalize_vote(raw_vote: object, index: int) -> JudgeVote:
        if isinstance(raw_vote, JudgeVote):
            judge_id = raw_vote.judge_id
            worker_pct = raw_vote.worker_pct
            reasoning = raw_vote.reasoning
            voted_at = raw_vote.voted_at
        elif isinstance(raw_vote, dict):
            worker_pct_value = raw_vote.get("worker_pct")
            reasoning_value = raw_vote.get("reasoning")
            judge_id_value = raw_vote.get("judge_id")
            voted_at_value = raw_vote.get("voted_at")
            judge_id = str(judge_id_value) if isinstance(judge_id_value, str) else ""
            voted_at = str(voted_at_value) if isinstance(voted_at_value, str) else ""
            worker_pct = worker_pct_value if isinstance(worker_pct_value, int) else 50
            reasoning = (
                str(reasoning_value)
                if isinstance(reasoning_value, str) and reasoning_value.strip() != ""
                else "No reasoning provided."
            )
        else:
            raise ValueError("Judge returned unsupported vote type")

        if worker_pct < 0:
            worker_pct = 0
        elif worker_pct > 100:
            worker_pct = 100
        if reasoning.strip() == "":
            reasoning = "No reasoning provided."
        if judge_id.strip() == "":
            judge_id = f"judge-{index}"
        if voted_at.strip() == "":
            voted_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

        return JudgeVote(
            judge_id=judge_id,
            worker_pct=worker_pct,
            reasoning=reasoning,
            voted_at=voted_at,
        )

    @staticmethod
    def _delivery_rating(worker_pct: int) -> str:
        if worker_pct >= 80:
            return "extremely_satisfied"
        if worker_pct >= 40:
            return "satisfied"
        return "dissatisfied"

    @staticmethod
    def _spec_rating(worker_pct: int) -> str:
        if worker_pct >= 80:
            return "dissatisfied"
        if worker_pct >= 40:
            return "satisfied"
        return "extremely_satisfied"

    def _validate_ruling_preconditions(self, dispute_id: str) -> dict[str, Any]:
        dispute = self._store.get_dispute(dispute_id)
        if dispute is None:
            raise ServiceError("dispute_not_found", "Dispute not found", 404, {})

        if str(dispute["status"]) == "ruled" or dispute["ruled_at"] is not None:
            raise ServiceError(
                "dispute_already_ruled",
                "Dispute has already been ruled",
                409,
                {},
            )

        status = str(dispute["status"])
        if status not in {"rebuttal_pending", "rebuttal_submitted"}:
            raise ServiceError(
                "dispute_not_ready",
                "Dispute is not ready for ruling",
                409,
                {},
            )

        return dispute

    def _build_context(self, dispute: dict[str, Any], task_data: dict[str, Any]) -> DisputeContext:
        return DisputeContext(
            task_spec=str(task_data.get("spec", "")),
            deliverables=self._normalize_deliverables(task_data.get("deliverables")),
            claim=str(dispute["claim"]),
            rebuttal=str(dispute["rebuttal"]) if dispute["rebuttal"] is not None else None,
            task_title=str(task_data.get("title", "")),
            reward=int(task_data.get("reward", 0)),
        )

    async def _evaluate_judges(
        self,
        judges: list[Judge],
        context: DisputeContext,
    ) -> list[JudgeVote]:
        if len(judges) == 0:
            raise ServiceError("judge_unavailable", "No judges configured", 502, {})

        normalized_votes: list[JudgeVote] = []
        for index, judge in enumerate(judges):
            try:
                raw_vote = await judge.evaluate(context)
            except Exception as exc:
                raise ServiceError(
                    "judge_unavailable",
                    f"Judge {index} failed to evaluate dispute",
                    502,
                    {},
                ) from exc
            normalized_votes.append(self._normalize_vote(raw_vote, index))

        return normalized_votes

    @staticmethod
    def _compute_ruling(votes: list[JudgeVote]) -> tuple[int, str]:
        sorted_worker_pcts = sorted(v.worker_pct for v in votes)
        median_worker_pct = sorted_worker_pcts[len(sorted_worker_pcts) // 2]
        ruling_summary = "\n\n".join(v.reasoning for v in votes)
        return median_worker_pct, ruling_summary

    async def _split_escrow(
        self,
        platform_agent: PlatformAgent,
        dispute: dict[str, Any],
        median_worker_pct: int,
    ) -> None:
        try:
            await platform_agent.split_escrow(
                str(dispute["escrow_id"]),
                str(dispute["respondent_id"]),
                str(dispute["claimant_id"]),
                median_worker_pct,
            )
        except httpx.HTTPStatusError as exc:
            raise ServiceError(
                "central_bank_unavailable",
                "Cannot reach Central Bank service",
                502,
                {},
            ) from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "central_bank_unavailable",
                "Cannot reach Central Bank service",
                502,
                {},
            ) from exc

    async def _record_feedback(
        self,
        platform_agent: PlatformAgent,
        dispute: dict[str, Any],
        median_worker_pct: int,
        ruling_summary: str,
    ) -> None:
        platform_agent_id = platform_agent.agent_id or ""

        spec_feedback_payload: dict[str, object] = {
            "action": "submit_feedback",
            "task_id": str(dispute["task_id"]),
            "from_agent_id": platform_agent_id,
            "to_agent_id": str(dispute["claimant_id"]),
            "category": "spec_quality",
            "rating": self._spec_rating(median_worker_pct),
            "comment": ruling_summary,
        }
        delivery_feedback_payload: dict[str, object] = {
            "action": "submit_feedback",
            "task_id": str(dispute["task_id"]),
            "from_agent_id": platform_agent_id,
            "to_agent_id": str(dispute["respondent_id"]),
            "category": "delivery_quality",
            "rating": self._delivery_rating(median_worker_pct),
            "comment": ruling_summary,
        }

        try:
            await platform_agent.submit_platform_feedback(spec_feedback_payload)
            await platform_agent.submit_platform_feedback(delivery_feedback_payload)
        except httpx.HTTPStatusError as exc:
            raise ServiceError(
                "reputation_service_unavailable",
                "Cannot reach Reputation service",
                502,
                {},
            ) from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "reputation_service_unavailable",
                "Cannot reach Reputation service",
                502,
                {},
            ) from exc

    async def _record_task_ruling(
        self,
        platform_agent: PlatformAgent,
        dispute: dict[str, Any],
        dispute_id: str,
        median_worker_pct: int,
        ruling_summary: str,
    ) -> None:
        try:
            await platform_agent.record_ruling(
                str(dispute["task_id"]),
                {
                    "action": "record_ruling",
                    "task_id": str(dispute["task_id"]),
                    "ruling_id": dispute_id,
                    "worker_pct": median_worker_pct,
                    "ruling_summary": ruling_summary,
                },
            )
        except httpx.HTTPStatusError as exc:
            raise ServiceError(
                "task_board_unavailable",
                "Cannot reach Task Board service",
                502,
                {},
            ) from exc
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "task_board_unavailable",
                "Cannot reach Task Board service",
                502,
                {},
            ) from exc

    async def execute_ruling(
        self,
        dispute_id: str,
        judges: list[Judge],
        task_data: dict[str, Any],
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Evaluate dispute via judges and commit ruled outcome with side-effects."""
        dispute = self._validate_ruling_preconditions(dispute_id)

        self._store.set_status(dispute_id, "judging")

        try:
            context = self._build_context(dispute, task_data)
            normalized_votes = await self._evaluate_judges(judges, context)
            median_worker_pct, ruling_summary = self._compute_ruling(normalized_votes)

            await self._split_escrow(platform_agent, dispute, median_worker_pct)
            await self._record_feedback(
                platform_agent,
                dispute,
                median_worker_pct,
                ruling_summary,
            )
            await self._record_task_ruling(
                platform_agent,
                dispute,
                dispute_id,
                median_worker_pct,
                ruling_summary,
            )

            vote_dicts = [
                {
                    "judge_id": vote.judge_id,
                    "worker_pct": vote.worker_pct,
                    "reasoning": vote.reasoning,
                    "voted_at": vote.voted_at,
                }
                for vote in normalized_votes
            ]
            self._store.persist_ruling(dispute_id, median_worker_pct, ruling_summary, vote_dicts)
        except ServiceError as exc:
            self._store.revert_to_rebuttal_pending(dispute_id)
            raise exc
        except Exception as exc:
            self._store.revert_to_rebuttal_pending(dispute_id)
            raise ServiceError(
                "judge_unavailable",
                "Failed to evaluate dispute",
                502,
                {},
            ) from exc

        ruled_dispute = self._store.get_dispute(dispute_id)
        if ruled_dispute is None:
            msg = "Failed to load ruled dispute"
            raise RuntimeError(msg)
        return ruled_dispute
