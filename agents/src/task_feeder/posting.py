"""Durable native task-publication coordination for the Task Feeder."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import TYPE_CHECKING, cast
from weakref import WeakKeyDictionary

import httpx
from service_auth.posting_journal import RawTask, TaskSourceSnapshot, ordered_entries

from base_agent.runtime_intents import RuntimeIntentError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from base_agent.agent import BaseAgent
    from base_agent.runtime_intent_models import RuntimeOwner
    from base_agent.runtime_postings import FileTaskPostingStore, FrozenPostingRecord
    from task_feeder.config import TaskFeederConfig

_STORE_LOCKS: WeakKeyDictionary[FileTaskPostingStore, asyncio.Lock] = WeakKeyDictionary()


def _store_lock(store: FileTaskPostingStore) -> asyncio.Lock:
    lock = _STORE_LOCKS.get(store)
    if lock is None:
        lock = asyncio.Lock()
        _STORE_LOCKS[store] = lock
    return lock


def _agent_binding(agent: BaseAgent) -> tuple[str, str, dict[str, str]]:
    agent_id = agent.agent_id
    if not isinstance(agent_id, str) or agent_id == "":
        raise RuntimeIntentError("owner_conflict", "agent must be registered for posting")
    encoded = agent.get_public_key_b64()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError) as exc:
        raise RuntimeIntentError("owner_conflict", "agent public key is invalid") from exc
    if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
        raise RuntimeIntentError("owner_conflict", "agent public key is not canonical")
    urls = {
        "identity_url": agent.config.identity_url,
        "bank_url": agent.config.bank_url,
        "task_board_url": agent.config.task_board_url,
        "reputation_url": agent.config.reputation_url,
        "court_url": agent.config.court_url,
    }
    return agent_id, hashlib.sha256(raw).hexdigest(), urls


def _store_owner(store: FileTaskPostingStore) -> RuntimeOwner:
    """Read the immutable owner through the store's lease-verifying accessor."""

    return store.owner


def _record_identity(record: FrozenPostingRecord) -> tuple[int, int]:
    return cast("int", record.selection_sequence), cast("int", record.revision)


class TaskFeederPostingCoordinator:
    """Serialize prepare, persistence, transport, and acknowledgment writes."""

    def __init__(self, agent: BaseAgent, store: FileTaskPostingStore) -> None:
        self._agent = agent
        self._store = store
        self._lock = _store_lock(store)
        self._snapshot: TaskSourceSnapshot | None = None
        self._source_ready = False
        self._published_count = 0
        self._assert_owner_binding()

    @property
    def source_ready(self) -> bool:
        return self._source_ready

    @property
    def published_count(self) -> int:
        return self._published_count

    def _assert_owner_binding(self) -> None:
        owner = _store_owner(self._store)
        agent_id, fingerprint, urls = _agent_binding(self._agent)
        if (
            agent_id != owner.registered_agent_id
            or fingerprint != owner.key_fingerprint
            or urls != owner.service_urls
        ):
            raise RuntimeIntentError("owner_conflict", "agent does not match posting owner")

    def rebuild_task_map(self) -> dict[str, RawTask]:
        """Reconstruct published task evidence from the durable ledger."""

        self._assert_owner_binding()
        rebuilt: dict[str, RawTask] = {}
        for record in self._store.records():
            if record.state != "published":
                continue
            rebuilt[record.intent.task_id] = RawTask.from_json(record.as_json()["raw_task"])
        self._published_count = len(rebuilt)
        return rebuilt

    def bind_current_source(self, snapshot: TaskSourceSnapshot, config: TaskFeederConfig) -> None:
        """Bind or compare the exact current source and posting terms."""

        self._source_ready = False
        self._snapshot = None
        self._assert_owner_binding()
        binding = {
            "source_path": str(snapshot.source_path),
            "source_snapshot_sha256": snapshot.source_snapshot_sha256,
            "source_size_bytes": snapshot.source_size_bytes,
            "source_entry_count": snapshot.source_entry_count,
            "posting_terms": {
                "base_reward": config.base_reward,
                "reward_per_level": config.reward_per_level,
                "bidding_deadline_seconds": config.bidding_deadline_seconds,
                "acceptance_window_seconds": config.acceptance_window_seconds,
                "execution_deadline_seconds": config.execution_deadline_seconds,
                "review_deadline_seconds": config.review_deadline_seconds,
                "shuffle": config.shuffle,
            },
        }
        self._store.bind_source(binding, source_bytes=snapshot.source_bytes)
        self._snapshot = snapshot
        self._source_ready = True

    async def replay_unfinished(self) -> tuple[FrozenPostingRecord, ...]:
        """Make one bounded status-first pass over saved retryable postings."""

        async with self._lock:
            self._assert_owner_binding()
            candidates = tuple(
                record
                for record in self._store.records()
                if record.state in {"prepared", "awaiting_lock", "attention_required"}
            )
            processed: list[FrozenPostingRecord] = []
            for record in candidates:
                processed.append(await self._resume_record(record))
            return tuple(processed)

    async def resume_one(self) -> FrozenPostingRecord | None:
        """Attempt the first saved retryable selection, if any."""

        async with self._lock:
            self._assert_owner_binding()
            record = next(
                (
                    candidate
                    for candidate in self._store.records()
                    if candidate.state in {"prepared", "awaiting_lock", "attention_required"}
                ),
                None,
            )
            if record is None:
                return None
            return await self._resume_record(record)

    async def reserve_and_submit_next(self) -> FrozenPostingRecord:
        """Persist one new deterministic selection, then submit its frozen intent."""

        async with self._lock:
            self._assert_owner_binding()
            snapshot = self._snapshot
            if not self._source_ready or snapshot is None:
                raise RuntimeIntentError("source_conflict", "current posting source is not ready")
            if any(
                record.state
                in {"prepared", "awaiting_lock", "attention_required", "blocked_integrity"}
                for record in self._store.records()
            ):
                raise RuntimeIntentError(
                    "unresolved_posting", "an unresolved posting blocks fresh selection"
                )
            head = self._store.head()
            if head is None:
                raise RuntimeIntentError("source_conflict", "posting source is not bound")
            binding = cast("Mapping[str, object]", head["source_binding"])
            sequence = cast("int", head["committed_selection_count"])
            entry_count = cast("int", binding["source_entry_count"])
            cycle_index, cycle_position = divmod(sequence, entry_count)
            entry = ordered_entries(snapshot, binding, cycle_index=cycle_index)[cycle_position]
            terms = cast("Mapping[str, object]", binding["posting_terms"])
            reward = cast("int", terms["base_reward"]) + entry.raw_task.level * cast(
                "int", terms["reward_per_level"]
            )
            intent = self._agent.prepare_task_posting(
                entry.raw_task.title,
                entry.raw_task.spec,
                reward,
                cast("int", terms["bidding_deadline_seconds"]),
                cast("int", terms["acceptance_window_seconds"]),
                cast("int", terms["execution_deadline_seconds"]),
                cast("int", terms["review_deadline_seconds"]),
            )
            record = self._store.reserve(entry.selection_json(), intent)
            return await self._submit(record)

    async def _resume_record(  # noqa: PLR0911
        self, record: FrozenPostingRecord
    ) -> FrozenPostingRecord:
        sequence, revision = _record_identity(record)
        try:
            response = await self._agent.get_task_posting(record.intent.operation_id)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 404:
                if record.state == "attention_required":
                    return record
                return await self._submit(record)
            return self._record_http_rejection(record, status)
        except httpx.RequestError:
            return self._store.record_diagnostic(
                sequence,
                revision,
                code="transport_error",
                status_code=None,
                attention=False,
            )
        except (RuntimeError, TypeError, ValueError):
            return self._store.record_diagnostic(
                sequence,
                revision,
                code="invalid_acknowledgment",
                status_code=None,
                attention=True,
            )
        try:
            observed = self._store.record_observation(
                sequence,
                revision,
                response,
                observation="status",
            )
        except RuntimeIntentError as exc:
            if exc.reason != "invalid_acknowledgment":
                raise
            return self._store.record_diagnostic(
                sequence,
                revision,
                code="invalid_acknowledgment",
                status_code=getattr(response, "status_code", None),
                attention=True,
            )
        if record.state == "attention_required" or observed.state != "awaiting_lock":
            return observed
        return await self._submit(observed)

    async def _submit(self, record: FrozenPostingRecord) -> FrozenPostingRecord:
        self._assert_owner_binding()
        sequence, revision = _record_identity(record)
        try:
            response = await self._agent.submit_task_posting(record.intent)
        except httpx.HTTPStatusError as exc:
            return self._record_http_rejection(record, exc.response.status_code)
        except httpx.RequestError:
            return self._store.record_diagnostic(
                sequence,
                revision,
                code="transport_error",
                status_code=None,
                attention=False,
            )
        except (RuntimeError, TypeError, ValueError):
            return self._store.record_diagnostic(
                sequence,
                revision,
                code="invalid_acknowledgment",
                status_code=None,
                attention=True,
            )
        try:
            return self._store.record_observation(
                sequence,
                revision,
                response,
                observation="submit",
            )
        except RuntimeIntentError as exc:
            if exc.reason != "invalid_acknowledgment":
                raise
            return self._store.record_diagnostic(
                sequence,
                revision,
                code="invalid_acknowledgment",
                status_code=getattr(response, "status_code", None),
                attention=True,
            )

    def _record_http_rejection(
        self, record: FrozenPostingRecord, status_code: int
    ) -> FrozenPostingRecord:
        if not 400 <= status_code <= 599:
            raise RuntimeIntentError(
                "invalid_acknowledgment", "HTTP rejection has an invalid status"
            )
        sequence, revision = _record_identity(record)
        return self._store.record_diagnostic(
            sequence,
            revision,
            code="http_rejection",
            status_code=status_code,
            attention=status_code < 500,
        )


__all__ = ["TaskFeederPostingCoordinator"]
