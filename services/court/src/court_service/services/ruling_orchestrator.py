"""Ruling orchestration for dispute evaluation side effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from service_commons.exceptions import ServiceError

from court_service.judges import DisputeContext, JudgeVote

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent

    from court_service.judges.base import Judge
    from court_service.services.protocol import DisputeStorageInterface


class DeliverableFetcherInterface(Protocol):
    """Fetches decoded deliverable texts for a task."""

    async def fetch(self, task_id: str) -> list[str]: ...


class DeliverableFetcher:
    """Reads task assets from Task Board and returns their text content.

    Task Board's ``GET /tasks/{id}`` does not carry deliverable bytes (GAP-A9), so
    this reads the task's uploaded assets from the public Task Board asset endpoints
    and returns their text content, capped by a configured byte budget. Lives here
    (rather than its own module) because only ``dispute_db_client``,
    ``ruling_orchestrator``, and ``disputes`` are permitted to import ``httpx``
    directly (see ``tests/architecture/test_db_client_isolation.py``).
    """

    def __init__(
        self,
        task_board_url: str,
        max_deliverable_bytes: int,
        timeout_seconds: int,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=task_board_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._max_deliverable_bytes = max_deliverable_bytes

    @staticmethod
    def _asset_ids(listing: dict[str, Any]) -> list[str]:
        assets = listing.get("assets")
        if not isinstance(assets, list):
            return []
        ids: list[str] = []
        for asset in assets:
            if isinstance(asset, dict):
                asset_id = asset.get("asset_id")
                if isinstance(asset_id, str) and asset_id != "":
                    ids.append(asset_id)
        return ids

    async def fetch(self, task_id: str) -> list[str]:
        """Return decoded deliverable texts, total size capped by config.

        Connection/transport errors propagate so a transient Task Board outage is
        retried by the caller rather than silently ruling on no evidence. Individual
        byte payloads are decoded leniently (never raising on non-UTF-8 content).
        """
        listing_response = await self._client.get(f"/tasks/{task_id}/assets")
        listing_response.raise_for_status()
        listing = listing_response.json()
        if not isinstance(listing, dict):
            return []

        remaining = self._max_deliverable_bytes
        texts: list[str] = []
        for asset_id in self._asset_ids(listing):
            if remaining <= 0:
                break
            asset_response = await self._client.get(f"/tasks/{task_id}/assets/{asset_id}")
            asset_response.raise_for_status()
            content = asset_response.content[:remaining]
            remaining -= len(content)
            texts.append(content.decode("utf-8", errors="replace"))
        return texts

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


class RulingOrchestrator:
    """Orchestrates judge evaluation and ruling side effects."""

    def __init__(
        self,
        store: DisputeStorageInterface,
        feedback_extremely_satisfied_cutoff: int,
        feedback_satisfied_cutoff: int,
        feedback_comment_max_length: int,
    ) -> None:
        self._store = store
        self._extremely_satisfied_cutoff = feedback_extremely_satisfied_cutoff
        self._satisfied_cutoff = feedback_satisfied_cutoff
        self._comment_max_length = feedback_comment_max_length

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

    def _delivery_rating(self, worker_pct: int) -> str:
        if worker_pct >= self._extremely_satisfied_cutoff:
            return "extremely_satisfied"
        if worker_pct >= self._satisfied_cutoff:
            return "satisfied"
        return "dissatisfied"

    def _spec_rating(self, worker_pct: int) -> str:
        if worker_pct >= self._extremely_satisfied_cutoff:
            return "dissatisfied"
        if worker_pct >= self._satisfied_cutoff:
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

        # GAP-A8/T-039: ruling requires a rebuttal on record OR the rebuttal
        # deadline having passed. Court's own status literal never actually reaches
        # "rebuttal_submitted" in production (only "rebuttal_pending" is set by
        # file_dispute, and update_rebuttal does not change status), so the
        # rebuttal's presence is read directly off the dispute rather than trusted
        # to a status value.
        if dispute["rebuttal"] is None and not self._rebuttal_window_closed(dispute):
            raise ServiceError(
                "dispute_not_ready",
                "Dispute is not ready for ruling",
                409,
                {},
            )

        return dispute

    @staticmethod
    def _rebuttal_window_closed(dispute: dict[str, Any]) -> bool:
        deadline_raw = dispute.get("rebuttal_deadline")
        if not isinstance(deadline_raw, str) or deadline_raw == "":
            return False
        deadline = datetime.fromisoformat(deadline_raw.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return datetime.now(UTC) >= deadline

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
            except ServiceError:
                raise
            except Exception as exc:
                raise ServiceError(
                    "judge_unavailable",
                    f"Judge {index} failed: {exc}",
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

    async def _record_feedback(
        self,
        platform_agent: PlatformAgent,
        dispute: dict[str, Any],
        median_worker_pct: int,
        ruling_summary: str,
    ) -> None:
        platform_agent_id = platform_agent.agent_id or ""
        comment = ruling_summary[: self._comment_max_length]

        spec_feedback_payload: dict[str, object] = {
            "action": "submit_feedback",
            "task_id": str(dispute["task_id"]),
            "from_agent_id": platform_agent_id,
            "to_agent_id": str(dispute["claimant_id"]),
            "category": "spec_quality",
            "rating": self._spec_rating(median_worker_pct),
            "comment": comment,
        }
        delivery_feedback_payload: dict[str, object] = {
            "action": "submit_feedback",
            "task_id": str(dispute["task_id"]),
            "from_agent_id": platform_agent_id,
            "to_agent_id": str(dispute["respondent_id"]),
            "category": "delivery_quality",
            "rating": self._delivery_rating(median_worker_pct),
            "comment": comment,
        }

        await self._submit_feedback_idempotent(platform_agent, spec_feedback_payload)
        await self._submit_feedback_idempotent(platform_agent, delivery_feedback_payload)

    @staticmethod
    def _is_feedback_exists(exc: httpx.HTTPStatusError) -> bool:
        """Report whether a Reputation error is a benign 'already recorded' 409."""
        if exc.response.status_code != 409:
            return False
        try:
            body = exc.response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("error") == "feedback_exists"

    async def _submit_feedback_idempotent(
        self,
        platform_agent: PlatformAgent,
        feedback_payload: dict[str, object],
    ) -> None:
        """Submit one feedback record, treating a 409 feedback_exists as success.

        On a retried ruling the record may already exist; that is convergent, not a
        failure. Every other Reputation error still surfaces as a 502 so the dispute
        stays recoverable for a later retry (T-040).
        """
        try:
            await platform_agent.submit_platform_feedback(feedback_payload)
        except httpx.HTTPStatusError as exc:
            if self._is_feedback_exists(exc):
                return
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

    def begin_ruling(self, dispute_id: str) -> dict[str, Any]:
        """Validate preconditions and mark the dispute ``judging``.

        Must run, and its status write must land, before any Task Board call that
        could re-enter this same disputed task's lazy evaluation (fetching the task
        or its deliverables): Task Board's read path re-triggers the GAP-A1 ruling
        trigger for any task still ``disputed``, so a reentrant call for the same
        dispute needs to see ``judging`` (not ``rebuttal_pending``/``rebuttal_submitted``)
        and fail fast with ``dispute_not_ready`` instead of recursing indefinitely
        between Court and Task Board.
        """
        dispute = self._validate_ruling_preconditions(dispute_id)
        self._store.set_status(dispute_id, "judging")
        return dispute

    async def finish_ruling(
        self,
        dispute_id: str,
        dispute: dict[str, Any],
        judges: list[Judge],
        task_data: dict[str, Any],
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Evaluate judges and commit the ruled outcome with side-effects.

        ``dispute`` must already be in the ``judging`` status via ``begin_ruling``.
        """
        try:
            context = self._build_context(dispute, task_data)
            normalized_votes = await self._evaluate_judges(judges, context)
            median_worker_pct, ruling_summary = self._compute_ruling(normalized_votes)

            await self._record_task_ruling(
                platform_agent,
                dispute,
                dispute_id,
                median_worker_pct,
                ruling_summary,
            )
            await self._record_feedback(
                platform_agent,
                dispute,
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

    async def execute_ruling(
        self,
        dispute_id: str,
        judges: list[Judge],
        task_data: dict[str, Any],
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Evaluate dispute via judges and commit ruled outcome with side-effects.

        Convenience wrapper over ``begin_ruling``/``finish_ruling`` for callers that
        already have ``task_data`` in hand (no reentrancy risk). The Court router
        calls the two phases directly so it can mark ``judging`` before fetching the
        task from Task Board.
        """
        dispute = self.begin_ruling(dispute_id)
        return await self.finish_ruling(dispute_id, dispute, judges, task_data, platform_agent)
