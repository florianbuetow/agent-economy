"""Dispatch caller-owned task intents through the public agent SDK."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from typing import TYPE_CHECKING, TypedDict, cast

import httpx
from service_auth import TaskBoardCommandContractError, TaskBoardCommandResponse

from base_agent.runtime_intents import (
    FileTaskIntentStore,
    FrozenTaskIntent,
    NewTaskIntent,
    RuntimeIntentError,
    RuntimeOwner,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from base_agent.agent import BaseAgent
    from base_agent.runtime_config import RuntimeStateConfig

logger = logging.getLogger(__name__)


class _NativeIdentity(TypedDict):
    operation_id: str
    expected_lifecycle_version: int


def _command_response(value: object) -> TaskBoardCommandResponse:
    if not isinstance(value, TaskBoardCommandResponse):
        raise RuntimeIntentError("invalid_input", "SDK returned an untyped command response")
    return value


def _agent_binding(agent: BaseAgent) -> tuple[str, str, dict[str, str]]:
    agent_id = agent.agent_id
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise RuntimeIntentError("invalid_input", "Agent must be registered before journal use")
    encoded = agent.get_public_key_b64()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise RuntimeIntentError("invalid_input", "Agent public key is not valid base64") from exc
    if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
        raise RuntimeIntentError("invalid_input", "Agent public key must be canonical raw Ed25519")
    urls = {
        "identity_url": agent.config.identity_url,
        "bank_url": agent.config.bank_url,
        "task_board_url": agent.config.task_board_url,
        "reputation_url": agent.config.reputation_url,
        "court_url": agent.config.court_url,
    }
    return agent_id, hashlib.sha256(raw).hexdigest(), urls


def runtime_owner_from_agent(
    *,
    runtime_config: RuntimeStateConfig,
    agent: BaseAgent,
    role: str,
    profile: str,
    handle: str,
) -> RuntimeOwner:
    """Bind exact deployment fields to the public SDK's loaded identity."""
    agent_id, fingerprint, urls = _agent_binding(agent)
    return RuntimeOwner(
        schema_version=1,
        environment_id=runtime_config.environment_id,
        role=role,
        profile=profile,
        handle=handle,
        registered_agent_id=agent_id,
        key_fingerprint=fingerprint,
        service_urls=urls,
    )


class RuntimeTaskCommands:
    """Serialize each local slot and send only its original durable command."""

    def __init__(self, agent: BaseAgent, store: FileTaskIntentStore) -> None:
        self._agent = agent
        self._store = store
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._verify_agent()

    def _verify_agent(self) -> None:
        try:
            agent_id, fingerprint, urls = _agent_binding(self._agent)
        except RuntimeIntentError as exc:
            raise RuntimeIntentError(
                "owner_conflict", "SDK identity is not the journal owner"
            ) from exc
        owner = self._store.owner
        if (
            agent_id != owner.registered_agent_id
            or fingerprint != owner.key_fingerprint
            or urls != owner.service_urls
        ):
            raise RuntimeIntentError("owner_conflict", "SDK identity is not the journal owner")

    def _slot_lock(self, task_id: str, slot: str) -> asyncio.Lock:
        return self._locks.setdefault((task_id, slot), asyncio.Lock())

    async def execute(
        self,
        *,
        task_id: str,
        slot: str,
        prepare: Callable[[], Awaitable[NewTaskIntent]],
    ) -> FrozenTaskIntent:
        """Look up first; prepare and persist only when this slot is absent."""
        async with self._slot_lock(task_id, slot):
            self._verify_agent()
            intent = self._store.load(task_id, slot)
            if intent is None:
                candidate = await prepare()
                self._verify_agent()
                if candidate.task_id != task_id or candidate.slot != slot:
                    raise RuntimeIntentError(
                        "invalid_input", "Prepared intent belongs to another slot"
                    )
                intent = self._store.create(candidate)
            return await self._dispatch(intent)

    async def replay_unfinished(self) -> tuple[FrozenTaskIntent, ...]:
        """Make one attempt per known pending slot without remote discovery."""
        self._verify_agent()
        results: list[FrozenTaskIntent] = []
        for saved in self._store.unfinished():
            async with self._slot_lock(saved.task_id, saved.slot):
                current = self._store.load(saved.task_id, saved.slot)
                if current is None:
                    raise RuntimeIntentError(
                        "corrupt_record", "Known intent disappeared before replay"
                    )
                results.append(await self._dispatch(current))
        return tuple(results)

    async def _dispatch(self, intent: FrozenTaskIntent) -> FrozenTaskIntent:
        if intent.state not in {"pending", "blocked"}:
            return intent
        self._verify_agent()
        status_code: int
        body: object
        try:
            response = await self._send(intent)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            try:
                decoded: object = exc.response.json()
            except ValueError:
                decoded = None
            body = decoded if isinstance(decoded, dict) else None
        except TaskBoardCommandContractError as exc:
            status_code, body = exc.status_code, None
        except httpx.RequestError:
            logger.warning(
                "Task command acknowledgment unavailable: task=%s slot=%s operation=%s",
                intent.task_id,
                intent.slot,
                intent.operation_id,
            )
            return intent
        else:
            response = _command_response(response)
            status_code, body = response.status_code, response.body
        return self._store.record_acknowledgment(
            task_id=intent.task_id,
            slot=intent.slot,
            expected_revision=intent.revision,
            status_code=status_code,
            body=body,
        )

    async def _send(self, intent: FrozenTaskIntent) -> TaskBoardCommandResponse:
        arguments = intent.public_arguments
        identity: _NativeIdentity = {
            "operation_id": intent.operation_id,
            "expected_lifecycle_version": intent.expected_lifecycle_version,
        }
        # The action-specific models already validate these scalar argument types.
        if intent.action == "submit_bid":
            return await self._agent.submit_bid(
                intent.task_id, cast("int", arguments["amount"]), **identity
            )
        if intent.action == "accept_bid":
            return await self._agent.accept_bid(
                intent.task_id, cast("str", arguments["bid_id"]), **identity
            )
        if intent.action == "submit_deliverable":
            return await self._agent.submit_deliverable(intent.task_id, **identity)
        if intent.action == "approve_task":
            return await self._agent.approve_task(intent.task_id, **identity)
        if intent.action == "dispute_task":
            return await self._agent.dispute_task(
                intent.task_id, cast("str", arguments["reason"]), **identity
            )
        raise RuntimeIntentError("invalid_input", "Unsupported runtime command")
