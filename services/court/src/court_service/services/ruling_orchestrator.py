"""Ruling orchestration for dispute evaluation side effects."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError
from service_commons.telemetry import annotate

from court_service.judges import DisputeContext, JudgeVote
from court_service.judges.output import QUALITY_RATINGS, median_rating, parse_judge_output
from court_service.services.evidence_bundle import (
    EvidenceFile,
    EvidencePolicy,
    EvidenceScreen,
    build_evidence_bundle,
    parse_asset_listing,
)
from court_service.services.ruling_acknowledgment import (
    task_version_conflict_from_response,
    validate_ruling_acknowledgment,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from service_auth.platform import PlatformAgent

    from court_service.judges.base import Judge
    from court_service.services.protocol import DisputeStorageInterface

    TaskLoader = Callable[[str], Awaitable[dict[str, Any]]]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RulingClaim:
    """One run's ownership of a dispute: the judging lease it holds."""

    dispute: dict[str, Any]
    lease_id: str
    decided: bool


@dataclass(frozen=True)
class RulingDecision:
    """The persisted decision plus the request snapshot every delivery replays."""

    worker_pct: int
    summary: str
    spec_quality: str
    delivery_quality: str
    expected_lifecycle_version: int
    reward: int
    acknowledgments: frozenset[str]


class TokenSigner(Protocol):
    """Signs platform JWS tokens (``PlatformAgent.sign``)."""

    def sign(self, payload: dict[str, Any], *, audience: str, resource: str) -> str: ...


class DeliverableFetcherInterface(Protocol):
    """Fetches the task's classified evidence files."""

    async def fetch(self, task_id: str) -> list[EvidenceFile]: ...


class DeliverableFetcher:
    """Reads task assets from Task Board into classified evidence files (plan 3.1).

    Task Board's ``GET /tasks/{id}`` does not carry deliverable bytes (GAP-A9), so
    this reads the task's uploaded assets from the Task Board asset endpoints
    (authenticated with a platform-signed Bearer token). Every listed asset stays
    in the result: oversized or unsupported files are not downloaded and carry an
    explicit status, downloaded bytes must match the listed sha256, and invalid
    UTF-8 is marked undecodable instead of being replaced. Lives here (rather
    than its own module) because only ``dispute_db_client``,
    ``ruling_orchestrator``, and ``disputes`` are permitted to import ``httpx``
    directly (see ``tests/architecture/test_db_client_isolation.py``).
    """

    def __init__(
        self,
        task_board_url: str,
        policy: EvidencePolicy,
        timeout_seconds: int,
        signer: TokenSigner,
    ) -> None:
        self._signer = signer
        self._client = httpx.AsyncClient(
            base_url=task_board_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        self._policy = policy

    def _auth(self, payload: dict[str, Any], resource: str) -> dict[str, str]:
        token = self._signer.sign(payload, audience="task-board", resource=resource)
        return {"Authorization": f"Bearer {token}"}

    async def fetch(self, task_id: str) -> list[EvidenceFile]:
        """Return every listed asset as an evidence file, in listing order.

        Transport errors propagate so a transient Task Board outage is retried by
        the caller rather than silently ruling on no evidence; a malformed listing
        or a hash mismatch raises an explicit ``ServiceError``.
        """
        listing_response = await self._client.get(
            f"/tasks/{task_id}/assets",
            headers=self._auth(
                {"action": "list_assets", "task_id": task_id}, f"GET /tasks/{task_id}/assets"
            ),
        )
        listing_response.raise_for_status()
        screen = EvidenceScreen(self._policy)
        files: list[EvidenceFile] = []
        for asset in parse_asset_listing(listing_response.json()):
            excluded = screen.exclusion(asset)
            if excluded is not None:
                files.append(excluded)
                continue
            asset_id = asset.asset_id
            asset_response = await self._client.get(
                f"/tasks/{task_id}/assets/{asset_id}",
                headers=self._auth(
                    {"action": "download_asset", "task_id": task_id, "asset_id": asset_id},
                    f"GET /tasks/{task_id}/assets/{asset_id}",
                ),
            )
            asset_response.raise_for_status()
            files.append(screen.admit(asset, asset_response.content))
        return files

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
        judging_lease_seconds: int,
    ) -> None:
        self._store = store
        self._lease_seconds = judging_lease_seconds
        self._extremely_satisfied_cutoff = feedback_extremely_satisfied_cutoff
        self._satisfied_cutoff = feedback_satisfied_cutoff
        self._comment_max_length = feedback_comment_max_length

    @staticmethod
    def _task_snapshot_version(task_data: dict[str, Any]) -> int:
        version = task_data.get("lifecycle_version")
        if type(version) is not int or version < 0 or version > (1 << 63) - 1:
            raise ServiceError(
                "invalid_task_snapshot",
                "Task Board returned an invalid lifecycle version",
                502,
                {},
            )
        return version

    @staticmethod
    def _normalize_vote(raw_vote: object, index: int) -> JudgeVote:
        """Validate one vote strictly (plan 3.2): malformed raises, nothing defaults.

        ``JudgeVote`` instances come from judges that already validated their
        output; dict votes go through the same versioned schema check. Only the
        judge's identity and timestamp, which are not judge findings, fall back.
        """
        if isinstance(raw_vote, JudgeVote):
            return JudgeVote.from_findings(
                raw_vote.judge_id,
                parse_judge_output({"schema_version": 1, **raw_vote.as_record()}),
                raw_vote.voted_at,
            )
        if not isinstance(raw_vote, dict):
            raise ValueError("Judge returned unsupported vote type")
        findings = parse_judge_output(raw_vote)
        judge_id = raw_vote.get("judge_id")
        voted_at = raw_vote.get("voted_at")
        return JudgeVote.from_findings(
            judge_id if isinstance(judge_id, str) and judge_id.strip() else f"judge-{index}",
            findings,
            voted_at
            if isinstance(voted_at, str) and voted_at.strip()
            else datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        )

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
        if status == "judging":
            # Already admitted once; the Gateway decides whether its lease is
            # live (409) or expired/released (reclaim and resume).
            return dispute
        if status != "rebuttal_pending":
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
            task_title=str(task_data.get("title", "")),
            reward=int(task_data.get("reward", 0)),
            evidence=build_evidence_bundle(task_data, dispute),
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
                # Never echo provider text (plan 0.8); keep it in server logs only.
                _logger.exception("Judge evaluation failed", extra={"judge_index": index})
                raise ServiceError(
                    "judge_unavailable",
                    f"Judge {index} failed",
                    502,
                    {},
                ) from exc
            try:
                normalized_votes.append(self._normalize_vote(raw_vote, index))
            except ValueError as exc:
                # A malformed vote fails the whole panel (plan 3.2); no default 50.
                _logger.exception("Judge returned a malformed vote", extra={"judge_index": index})
                raise ServiceError("judge_unavailable", f"Judge {index} failed", 502, {}) from exc

        return normalized_votes

    @staticmethod
    def _panel_quality(votes: list[JudgeVote]) -> tuple[str, str]:
        """Median spec and delivery findings, independent of the payout median."""
        return (
            median_rating([v.spec_quality for v in votes]),
            median_rating([v.delivery_quality for v in votes]),
        )

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
        decision: RulingDecision,
    ) -> None:
        platform_agent_id = platform_agent.agent_id or ""
        comment = decision.summary[: self._comment_max_length]

        spec_feedback_payload: dict[str, object] = {
            "action": "submit_feedback",
            "task_id": str(dispute["task_id"]),
            "from_agent_id": platform_agent_id,
            "to_agent_id": str(dispute["claimant_id"]),
            "category": "spec_quality",
            "rating": decision.spec_quality,
            "comment": comment,
        }
        delivery_feedback_payload: dict[str, object] = {
            "action": "submit_feedback",
            "task_id": str(dispute["task_id"]),
            "from_agent_id": platform_agent_id,
            "to_agent_id": str(dispute["respondent_id"]),
            "category": "delivery_quality",
            "rating": decision.delivery_quality,
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
        expected_lifecycle_version: int,
        reward: int,
    ) -> None:
        operation_id = operation_id_for("court-ruling", (dispute_id,))
        task_id = str(dispute["task_id"])
        try:
            acknowledgment = await platform_agent.record_ruling(
                task_id,
                {
                    "action": "record_ruling",
                    "operation_id": operation_id,
                    "expected_lifecycle_version": expected_lifecycle_version,
                    "task_id": task_id,
                    "ruling_id": dispute_id,
                    "worker_pct": median_worker_pct,
                    "ruling_summary": ruling_summary,
                },
            )
            validate_ruling_acknowledgment(
                acknowledgment,
                operation_id=operation_id,
                task_id=task_id,
                source_version=expected_lifecycle_version,
                ruling_id=dispute_id,
                poster_id=str(dispute["claimant_id"]),
                worker_id=str(dispute["respondent_id"]),
                worker_pct=median_worker_pct,
                ruling_summary=ruling_summary,
                reward=reward,
            )
        except httpx.HTTPStatusError as exc:
            _logger.warning("Task Board rejected the ruling delivery", exc_info=True)
            try:
                error_body: object = exc.response.json()
            except ValueError:
                error_body = None
            version_conflict = task_version_conflict_from_response(
                exc.response.status_code,
                error_body,
                expected_version=expected_lifecycle_version,
            )
            if version_conflict is not None:
                raise version_conflict from exc
            raise ServiceError(
                "task_board_unavailable",
                "Cannot reach Task Board service",
                502,
                {},
            ) from exc
        except ServiceError:
            raise
        except Exception as exc:
            _logger.warning("Task Board ruling delivery failed", exc_info=True)
            raise ServiceError(
                "task_board_unavailable",
                "Cannot reach Task Board service",
                502,
                {},
            ) from exc

    def begin_ruling(self, dispute_id: str) -> RulingClaim:
        """Validate preconditions and take the judging lease (compare-and-set).

        The Gateway moves ``rebuttal_pending`` -> ``judging`` (or reclaims an
        expired/released ``judging`` lease) atomically, so exactly one run owns a
        dispute. Must land before any Task Board call that could re-enter this
        dispute's ruling trigger: a reentrant attempt meets the live lease and
        fails fast with ``dispute_not_ready``.
        """
        dispute = self._validate_ruling_preconditions(dispute_id)
        lease_id = f"lease-{uuid.uuid4()}"
        lease = self._store.acquire_judging_lease(dispute_id, lease_id, self._lease_seconds)
        return RulingClaim(dispute=dispute, lease_id=lease_id, decided=bool(lease["decided"]))

    def _release(self, dispute_id: str, lease_id: str) -> None:
        """Give up the lease so a retry resumes at once; never deletes a decision."""
        try:
            self._store.release_judging_lease(dispute_id, lease_id)
        except (ServiceError, RuntimeError, httpx.HTTPError):
            # Best effort: an unreleased lease simply expires and is reclaimed.
            _logger.exception("Failed to release judging lease", extra={"dispute_id": dispute_id})

    async def _decide(
        self,
        dispute_id: str,
        claim: RulingClaim,
        judges: list[Judge],
        task_data: dict[str, Any],
    ) -> RulingDecision:
        expected_lifecycle_version = self._task_snapshot_version(task_data)
        context = self._build_context(claim.dispute, task_data)
        votes = await self._evaluate_judges(judges, context)
        worker_pct, summary = self._compute_ruling(votes)
        spec_quality, delivery_quality = self._panel_quality(votes)
        # The quality findings ride in the persisted decision so a resumed
        # delivery replays them without asking the judges again (plan 3.2).
        request: dict[str, Any] = {
            "expected_lifecycle_version": expected_lifecycle_version,
            "reward": context.reward,
            "spec_quality": spec_quality,
            "delivery_quality": delivery_quality,
        }
        vote_dicts: list[dict[str, Any]] = [vote.as_record() for vote in votes]
        await asyncio.to_thread(
            self._store.claim_decision,
            dispute_id,
            claim.lease_id,
            worker_pct,
            summary,
            vote_dicts,
            request,
        )
        return RulingDecision(
            worker_pct=worker_pct,
            summary=summary,
            spec_quality=spec_quality,
            delivery_quality=delivery_quality,
            expected_lifecycle_version=expected_lifecycle_version,
            reward=context.reward,
            acknowledgments=frozenset(),
        )

    async def _stored_decision(self, dispute_id: str) -> RulingDecision:
        progress = await asyncio.to_thread(self._store.get_ruling_progress, dispute_id)
        decision = progress.get("decision")
        request = decision.get("request") if isinstance(decision, dict) else None
        if (
            not isinstance(decision, dict)
            or not isinstance(request, dict)
            or request.get("spec_quality") not in QUALITY_RATINGS
            or request.get("delivery_quality") not in QUALITY_RATINGS
        ):
            raise ServiceError(
                "ruling_state_invalid", "Stored ruling decision is incomplete", 500, {}
            )
        acknowledgments = progress.get("acknowledgments")
        steps = acknowledgments if isinstance(acknowledgments, list) else []
        return RulingDecision(
            worker_pct=int(decision["worker_pct"]),
            summary=str(decision["summary"]),
            spec_quality=str(request["spec_quality"]),
            delivery_quality=str(request["delivery_quality"]),
            expected_lifecycle_version=int(request["expected_lifecycle_version"]),
            reward=int(request["reward"]),
            acknowledgments=frozenset(str(step) for step in steps),
        )

    async def _deliver(
        self,
        dispute_id: str,
        claim: RulingClaim,
        decision: RulingDecision,
        platform_agent: PlatformAgent,
    ) -> None:
        """Replay the stored decision to every pending consumer, recording each ack."""
        if "task_board" not in decision.acknowledgments:
            await self._record_task_ruling(
                platform_agent,
                claim.dispute,
                dispute_id,
                decision.worker_pct,
                decision.summary,
                decision.expected_lifecycle_version,
                decision.reward,
            )
            await asyncio.to_thread(
                self._store.record_acknowledgment, dispute_id, claim.lease_id, "task_board"
            )
        if "reputation" not in decision.acknowledgments:
            await self._record_feedback(platform_agent, claim.dispute, decision)
            await asyncio.to_thread(
                self._store.record_acknowledgment, dispute_id, claim.lease_id, "reputation"
            )
        await asyncio.to_thread(self._store.complete_ruling, dispute_id, claim.lease_id)

    async def finish_ruling(
        self,
        dispute_id: str,
        claim: RulingClaim,
        judges: list[Judge],
        task_data: dict[str, Any] | None,
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Judge (unless a decision is stored), persist, then deliver idempotently.

        The decision is claimed in the Gateway before any Task Board or Reputation
        call. A delivery failure keeps the decision and releases the lease, so a
        retry (or the reclaim sweep) resumes the pending acknowledgments with the
        stored payload and never calls the judges again.
        """
        try:
            if claim.decided:
                decision = await self._stored_decision(dispute_id)
            else:
                if task_data is None:
                    msg = "task_data is required to judge an undecided dispute"
                    raise RuntimeError(msg)
                decision = await self._decide(dispute_id, claim, judges, task_data)
        except ServiceError:
            await asyncio.to_thread(self._release, dispute_id, claim.lease_id)
            raise
        except Exception as exc:
            _logger.exception("Ruling evaluation failed", extra={"dispute_id": dispute_id})
            await asyncio.to_thread(self._release, dispute_id, claim.lease_id)
            raise ServiceError("judge_unavailable", "Failed to evaluate dispute", 502, {}) from exc

        try:
            await self._deliver(dispute_id, claim, decision, platform_agent)
        except ServiceError:
            await asyncio.to_thread(self._release, dispute_id, claim.lease_id)
            raise
        except Exception as exc:
            _logger.exception("Ruling delivery failed", extra={"dispute_id": dispute_id})
            await asyncio.to_thread(self._release, dispute_id, claim.lease_id)
            raise ServiceError(
                "ruling_delivery_failed", "Failed to deliver ruling", 502, {}
            ) from exc

        ruled_dispute = await asyncio.to_thread(self._store.get_dispute, dispute_id)
        if ruled_dispute is None:
            msg = "Failed to load ruled dispute"
            raise RuntimeError(msg)
        return ruled_dispute

    async def rule(
        self,
        dispute_id: str,
        judges: list[Judge],
        platform_agent: PlatformAgent,
        load_task: TaskLoader,
    ) -> dict[str, Any]:
        """Take the lease, load the task only if judging is still needed, finish."""
        annotate(dispute_id=dispute_id, action="rule")
        claim = await asyncio.to_thread(self.begin_ruling, dispute_id)
        task_data: dict[str, Any] | None = None
        if not claim.decided:
            try:
                task_data = await load_task(str(claim.dispute["task_id"]))
            except ServiceError:
                await asyncio.to_thread(self._release, dispute_id, claim.lease_id)
                raise
            except Exception as exc:
                await asyncio.to_thread(self._release, dispute_id, claim.lease_id)
                raise ServiceError(
                    "task_board_unavailable", "Cannot reach Task Board service", 502, {}
                ) from exc
        return await self.finish_ruling(dispute_id, claim, judges, task_data, platform_agent)

    async def reclaim_stalled_rulings(
        self,
        judges: list[Judge],
        platform_agent: PlatformAgent,
        load_task: TaskLoader,
    ) -> list[str]:
        """Resume every ``judging`` dispute whose lease expired or was released.

        Live leases answer ``dispute_not_ready`` and are skipped; each failure is
        logged and left for the next sweep. Returns the dispute ids now ruled.
        """
        stalled = await asyncio.to_thread(self._store.list_disputes, None, "judging")
        resumed: list[str] = []
        for summary in stalled:
            dispute_id = str(summary["dispute_id"])
            try:
                await self.rule(dispute_id, judges, platform_agent, load_task)
            except ServiceError as exc:
                if exc.error != "dispute_not_ready":
                    _logger.warning(
                        "Stalled ruling not resumed",
                        extra={"dispute_id": dispute_id, "error": exc.error},
                    )
                continue
            resumed.append(dispute_id)
        return resumed

    async def execute_ruling(
        self,
        dispute_id: str,
        judges: list[Judge],
        task_data: dict[str, Any],
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """Run a ruling for callers that already hold ``task_data`` (no reentrancy)."""
        claim = self.begin_ruling(dispute_id)
        return await self.finish_ruling(dispute_id, claim, judges, task_data, platform_agent)
