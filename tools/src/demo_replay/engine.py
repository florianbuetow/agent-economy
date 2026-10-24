"""Scenario engine — loads YAML, executes steps sequentially with delays."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

import httpx
import yaml
from rich.console import Console
from rich.panel import Panel
from service_auth.financial_identity import operation_id_for
from service_auth.financial_responses import (
    SQLITE_MAX_INTEGER,
    LegacyFinancialEnvelope,
    NativeFinancialEnvelope,
)
from service_auth.signing import keypair_fingerprint
from service_auth.task_postings import TaskPostingIntent

from demo_replay import clients
from demo_replay.task_operations import (
    PHASES,
    TaskIntent,
    canonical_json,
    completed_bid,
    completed_mutation,
    exact_integer,
    posting_terms,
    required_text,
    step_identity,
)
from demo_replay.wallet import DemoAgent

if TYPE_CHECKING:
    from demo_replay.config import DemoReplayConfig, PlatformConfig

from demo_replay.run_store import ActorDefinition, RunEnvironment, RunStore, StoredStep

console = Console()


class UnknownActionError(ValueError):
    """Raised when a scenario step names an action the engine cannot dispatch."""


def load_scenario(path: Path) -> dict[str, Any]:
    """Load and validate a YAML scenario file."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        msg = f"Invalid scenario file: {path}"
        raise ValueError(msg)
    for required in ("name", "agents", "steps"):
        if required not in raw:
            msg = f"Scenario missing required key: {required}"
            raise ValueError(msg)
    for step in raw["steps"]:
        if isinstance(step, dict) and step.get("action") == "post_task":
            posting_terms(step)
    return raw


class ReplayEngine:
    """Execute a demo scenario against live services."""

    def __init__(self, scenario: dict[str, Any], config: PlatformConfig) -> None:
        self.scenario = scenario
        self._config = config
        self.default_delay: float = float(scenario.get("default_delay", 2.0))
        self.agents: dict[str, DemoAgent] = {}
        self.platform: DemoAgent | None = None
        # task tracking: poster_handle -> most recent task_id
        self._latest_task: dict[str, str] = {}
        # named refs: ref_name -> task_id
        self._refs: dict[str, str] = {}
        # bid tracking: task_id -> list of bid responses
        self._bids: dict[str, list[dict[str, Any]]] = {}
        # worker_handle -> task_id (set when bid is accepted)
        self._worker_task: dict[str, str] = {}
        # task_id -> Court dispute_id
        self._disputes: dict[str, str] = {}
        self._scenario_steps = tuple(scenario["steps"])
        self._intents: dict[int, TaskIntent] = {}
        self._active_step_index: int | None = None
        self._run_id: str | None = None
        self._scenario_bytes: bytes | None = None
        self._demo_config: DemoReplayConfig | None = None
        self._effective_delay_seconds: float | None = None
        self._store: RunStore | None = None

    def bind_durable_run(
        self,
        *,
        run_id: str,
        scenario_bytes: bytes,
        demo_config: DemoReplayConfig,
        effective_delay_seconds: float,
    ) -> None:
        """Bind the explicit durable scope required by the production CLI."""
        if self._run_id is not None:
            raise RuntimeError("Replay engine durable run is already bound")
        self._run_id = run_id
        self._scenario_bytes = scenario_bytes
        self._demo_config = demo_config
        self._effective_delay_seconds = effective_delay_seconds
        self.default_delay = effective_delay_seconds

    def _require_store(self) -> RunStore:
        if self._store is None:
            raise RuntimeError("Replay engine requires an explicit durable run")
        return self._store

    def _restore_completed_step(self, index: int, step: dict[str, Any]) -> bool:
        stored = self._require_store().step(index)
        if stored is None or stored.state != "completed" or stored.result is None:
            return False
        action = step["action"]
        if action == "post_task":
            body = stored.result.get("body")
            if not isinstance(body, dict):
                raise RuntimeError("Completed posting result is missing its body")
            task_id = required_text(body, "task_id")
            poster = required_text(step, "poster")
            self._latest_task[poster] = task_id
            if "ref" in step:
                self._refs[required_text(step, "ref")] = task_id
        elif action == "bid":
            bid = stored.result.get("bid")
            bid_task_id = stored.result.get("task_id")
            if not isinstance(bid, dict) or not isinstance(bid_task_id, str):
                raise RuntimeError("Completed bid projection is invalid")
            tracked = self._bids.setdefault(bid_task_id, [])
            if all(item.get("bid_id") != bid.get("bid_id") for item in tracked):
                tracked.append(dict(bid))
        elif action == "accept_bid":
            bidder_handle = stored.result.get("bidder_handle")
            assigned_task_id = stored.result.get("task_id")
            if not isinstance(bidder_handle, str) or not isinstance(assigned_task_id, str):
                raise RuntimeError("Completed assignment projection is invalid")
            self._worker_task[bidder_handle] = assigned_task_id
        elif action in {"dispute", "file_claim"}:
            disputed_task_id = stored.result.get("task_id")
            dispute_id = stored.result.get("dispute_id")
            if not isinstance(disputed_task_id, str) or not isinstance(dispute_id, str):
                raise RuntimeError("Completed dispute projection is invalid")
            self._disputes[disputed_task_id] = dispute_id
        return True

    def _restore_native_intent(self, index: int, step: dict[str, Any]) -> None:
        if self._store is None:
            return
        stored = self._store.step(index)
        if stored is None or stored.state == "completed":
            return
        if stored.state in {"attention_required", "definitive_rejection"}:
            raise RuntimeError(f"Durable step {index} requires operator attention")
        if step["action"] not in PHASES:
            return
        if isinstance(stored.intent, TaskPostingIntent):
            raise RuntimeError("Native mutation stored a posting intent")
        intent = TaskIntent.from_json(dict(stored.intent))
        if intent.step_json != step_identity(step):
            raise RuntimeError("Stored native intent differs from the frozen scenario")
        self._intents[index] = intent

    def _mark_native_dispatching(self, step: dict[str, Any]) -> StoredStep:
        store = self._require_store()
        index = self._step_index(step)
        current = store.step(index)
        if current is None:
            raise RuntimeError("Native mutation intent was not durably reserved")
        return store.mark_dispatching(index=index, expected_revision=current.revision)

    def _complete_native_step(
        self,
        step: dict[str, Any],
        dispatched: StoredStep,
        result: dict[str, object],
    ) -> None:
        self._require_store().finish_step(
            index=self._step_index(step),
            expected_revision=dispatched.revision,
            state="completed",
            result=result,
            diagnostic=None,
        )

    def _begin_nonreplayable_step(
        self,
        step: dict[str, Any],
        *,
        actor_handle: str,
        intent: dict[str, object],
    ) -> StoredStep:
        store = self._require_store()
        index = self._step_index(step)
        current = store.reserve_step(
            index=index,
            step=step,
            actor_handle=actor_handle,
            intent=intent,
        )
        if current.state == "dispatching":
            store.finish_step(
                index=index,
                expected_revision=current.revision,
                state="attention_required",
                result={"reason": "ambiguous_dispatch"},
                diagnostic="ambiguous_send",
            )
            raise RuntimeError(f"Durable step {index} requires operator attention")
        if current.state in {"attention_required", "definitive_rejection"}:
            raise RuntimeError(f"Durable step {index} requires operator attention")
        if current.state == "completed":
            return current
        return store.mark_dispatching(index=index, expected_revision=current.revision)

    def _mark_attention(self, index: int, current: StoredStep, reason: str) -> None:
        store = self._require_store()
        if current.state == "dispatching":
            store.finish_step(
                index=index,
                expected_revision=current.revision,
                state="attention_required",
                result={"reason": reason},
                diagnostic=reason,
            )

    def _step_index(self, step: dict[str, Any]) -> int:
        if self._active_step_index is not None:
            index = self._active_step_index
            if self._scenario_steps[index] is not step:
                raise ValueError("Active scenario step identity changed")
            return index
        matches = [
            index for index, candidate in enumerate(self._scenario_steps) if candidate is step
        ]
        if len(matches) != 1:
            raise ValueError("A native mutation must identify one loaded scenario step")
        return matches[0]

    def _existing_intent(self, step: dict[str, Any], action: str) -> TaskIntent | None:
        intent = self._intents.get(self._step_index(step))
        if intent is not None:
            if intent.action != action or intent.step_json != step_identity(step):
                raise ValueError(
                    "The original scenario step intent changed; refusing a new request"
                )
            self._actor_for_intent(intent)
        return intent

    def _actor_for_intent(self, intent: TaskIntent) -> DemoAgent:
        actor = self.agents.get(intent.actor_handle)
        if actor is None or actor.agent_id is None:
            raise ValueError("The original task intent actor is unavailable")
        intent.verify_actor(
            actor.handle,
            actor.agent_id,
            actor.public_key_string(),
        )
        return actor

    async def _remember_intent(
        self,
        http: httpx.AsyncClient,
        step: dict[str, Any],
        action: str,
        actor: DemoAgent,
        task_id: str,
        arguments: dict[str, Any],
    ) -> TaskIntent:
        if actor.agent_id is None:
            raise ValueError("Task operation actor must be registered")
        observed = await clients.get_task(http, task_id, self._config.task_board_url)
        if not isinstance(observed, dict) or observed.get("task_id") != task_id:
            raise ValueError("Observed task identity does not match the intended task")
        version = exact_integer(
            observed.get("lifecycle_version"), "lifecycle_version", 0, SQLITE_MAX_INTEGER
        )
        if observed.get("status") != PHASES[action][1]:
            raise RuntimeError("Observed task is not in the source phase for a new intent")
        if observed.get("pending_transition") is not None or observed.get("legacy_reconciliations"):
            raise RuntimeError("Observed task has unresolved recovery ownership")
        for field in ("poster_id", "escrow_id"):
            required_text(observed, field)
        exact_integer(observed.get("reward"), "reward", 1, SQLITE_MAX_INTEGER)
        if action in {"submit_deliverable", "approve", "dispute"}:
            required_text(observed, "worker_id")
            exact_integer(observed.get("accepted_reward"), "accepted_reward", 1, SQLITE_MAX_INTEGER)
        intent = TaskIntent(
            action=action,
            step_json=step_identity(step),
            operation_id=f"op-{uuid.uuid4()}",
            expected_lifecycle_version=version,
            actor_handle=actor.handle,
            actor_id=actor.agent_id,
            actor_public_key=actor.public_key_string(),
            task_id=task_id,
            arguments_json=canonical_json(arguments),
            observed_task_json=canonical_json(observed),
        )
        self._require_store().reserve_step(
            index=self._step_index(step),
            step=step,
            actor_handle=actor.handle,
            intent=intent.as_json(),
        )
        self._intents[self._step_index(step)] = intent
        return intent

    def _resolve_task_id(self, step: dict[str, Any], agent_handle: str) -> str:
        """Resolve task_id from explicit task_ref, poster's latest, or worker assignment."""
        if "task_ref" in step:
            ref = step["task_ref"]
            if ref not in self._refs:
                msg = f"Unknown task_ref: {ref}"
                raise ValueError(msg)
            return self._refs[ref]
        if agent_handle in self._latest_task:
            return self._latest_task[agent_handle]
        if agent_handle in self._worker_task:
            return self._worker_task[agent_handle]
        msg = f"No task found for agent '{agent_handle}' and no task_ref specified"
        raise ValueError(msg)

    async def run(self) -> None:
        """Execute all scenario steps."""
        if (
            self._run_id is None
            or self._scenario_bytes is None
            or self._demo_config is None
            or self._effective_delay_seconds is None
        ):
            raise RuntimeError("Replay engine requires --run-id and durable replay configuration")
        name = self.scenario["name"]
        console.print(Panel(f"[bold cyan]{name}[/bold cyan]", title="Demo Replay"))

        # Load the real platform agent (must match keys known to Central Bank)
        self.platform = DemoAgent.from_pem(
            "platform",
            "Platform",
            self._config.platform_key_path,
            self._config.token_ttl_seconds,
        )
        environment = RunEnvironment(
            environment_id=self._demo_config.environment_id,
            identity_url=self._config.identity_url,
            bank_url=self._config.bank_url,
            task_board_url=self._config.task_board_url,
            reputation_url=self._config.reputation_url,
            court_url=self._config.court_url,
            platform_public_fingerprint_sha256=keypair_fingerprint(
                "platform",
                self._config.platform_key_path.parent,
                allow_legacy_private_mode=True,
            ),
        )
        actor_definitions = tuple(
            ActorDefinition(handle=item["handle"], name=item["name"])
            for item in self.scenario["agents"]
        )
        with RunStore.open(
            state_directory=self._demo_config.state_directory,
            run_id=self._run_id,
            environment=environment,
            scenario_bytes=self._scenario_bytes,
            scenario=self.scenario,
            actor_definitions=actor_definitions,
            effective_delay_seconds=self._effective_delay_seconds,
            max_record_bytes=self._demo_config.max_record_bytes,
            diagnostic_limit=self._demo_config.diagnostic_limit,
        ) as store:
            self._store = store
            for handle, stored in store.actors.items():
                agent = DemoAgent.from_pem(
                    handle,
                    stored.name,
                    stored.private_key_path,
                    self._config.token_ttl_seconds,
                )
                agent.agent_id = stored.agent_id
                self.agents[handle] = agent
            if store.completed:
                for index, step in enumerate(self._scenario_steps):
                    self._restore_completed_step(index, step)
                console.print(Panel("[bold green]Demo already complete.[/bold green]"))
                self._store = None
                return
            await self._run_in_store(store)
            self._store = None

    async def _run_in_store(self, store: RunStore) -> None:
        async with httpx.AsyncClient(timeout=30.0) as http:
            for agent in self.agents.values():
                agent.configure_task_board(http, self._config.task_board_url)
            # Register platform agent first
            console.print("[dim]Registering platform agent...[/dim]")
            assert self.platform is not None
            await clients.register_agent(http, self.platform, self._config.identity_url)
            console.print(f"  [green]Platform registered:[/green] {self.platform.agent_id}")

            for handle, stored in store.actors.items():
                if stored.agent_id is None:
                    continue
                actual = await clients.get_agent(http, stored.agent_id, self._config.identity_url)
                store.record_registration(actor_handle=handle, response=actual)

            total = len(self.scenario["steps"])
            for i, step in enumerate(self.scenario["steps"], 1):
                action = step["action"]
                delay = float(step.get("delay", self.default_delay))

                console.print(f"\n[bold][{i}/{total}][/bold] [yellow]{action}[/yellow]")
                if not self._restore_completed_step(i - 1, step):
                    await self._execute_step(http, step, step_index=i - 1)
                console.print(f"  [dim]waiting {delay}s...[/dim]")
                await asyncio.sleep(delay)

        console.print(Panel("[bold green]Demo complete![/bold green]"))

    async def _execute_step(
        self,
        http: httpx.AsyncClient,
        step: dict[str, Any],
        *,
        step_index: int | None = None,
    ) -> None:
        """Dispatch one sequential scenario position while retaining its retry owner."""
        previous = self._active_step_index
        if step_index is not None:
            if (
                not 0 <= step_index < len(self._scenario_steps)
                or self._scenario_steps[step_index] is not step
            ):
                raise ValueError("Scenario step position does not identify this step")
        else:
            matches = [
                index for index, candidate in enumerate(self._scenario_steps) if candidate is step
            ]
            if len(matches) > 1:
                raise ValueError("An aliased scenario step requires its explicit position")
            if matches:
                step_index = matches[0]
        if step_index is not None and step_index in self._intents:
            original = self._intents[step_index]
            if original.step_json != step_identity(step):
                raise ValueError(
                    "The original scenario step intent changed; refusing a new request"
                )
            self._actor_for_intent(original)
        self._active_step_index = step_index
        try:
            if step_index is not None:
                self._restore_native_intent(step_index, step)
            await self._dispatch_step(http, step)
        finally:
            self._active_step_index = previous

    async def _dispatch_step(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        """Dispatch a single step to the appropriate handler."""
        action = step["action"]
        match action:
            case "register":
                await self._do_register(http, step)
            case "fund":
                await self._do_fund(http, step)
            case "post_task":
                await self._do_post_task(http, step)
            case "bid":
                await self._do_bid(http, step)
            case "accept_bid":
                await self._do_accept_bid(http, step)
            case "upload_asset":
                await self._do_upload_asset(http, step)
            case "submit_deliverable":
                await self._do_submit_deliverable(http, step)
            case "approve":
                await self._do_approve(http, step)
            case "dispute":
                await self._do_dispute(http, step)
            case "file_claim":
                await self._do_file_claim(http, step)
            case "submit_rebuttal":
                await self._do_submit_rebuttal(http, step)
            case "trigger_ruling":
                await self._do_trigger_ruling(http, step)
            case "feedback":
                await self._do_feedback(http, step)
            case _:
                msg = f"Unknown scenario action: {action!r}"
                raise UnknownActionError(msg)

    async def _do_register(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        handle = step["agent"]
        agent = self.agents[handle]
        stored = self._require_store().actors[handle]
        if stored.registration is None:
            result = await clients.register_agent(http, agent, self._config.identity_url)
            self._require_store().record_registration(actor_handle=handle, response=result)
        else:
            assert stored.agent_id is not None
            agent.agent_id = stored.agent_id
            result = dict(stored.registration)
        console.print(f"  [green]Registered {agent.name}[/green] -> {result['agent_id']}")

        # Also create bank account via platform agent
        assert self.platform is not None
        assert agent.agent_id is not None
        account = await clients.create_account(
            http, self.platform, agent.agent_id, self._config.bank_url
        )
        if account == {"status": "already_exists"}:
            await clients.get_account(http, agent, self._config.bank_url)
        console.print(f"  [green]Bank account created[/green] for {agent.name}")

    async def _do_fund(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        handle = step["agent"]
        amount = int(step["amount"])
        reference = step.get("reference")
        if not isinstance(reference, str) or reference == "":
            msg = "fund step requires a nonempty reference"
            raise ValueError(msg)
        agent = self.agents[handle]
        assert self.platform is not None
        assert agent.agent_id is not None
        operation_id = operation_id_for("demo-funding", (agent.agent_id, reference))
        store = self._require_store()
        index = self._step_index(step)
        reserved = store.reserve_step(
            index=index,
            step=step,
            actor_handle=handle,
            intent={
                "operation_id": operation_id,
                "account_id": agent.agent_id,
                "amount": amount,
                "reference": reference,
            },
        )
        if reserved.state == "completed":
            return
        dispatched = store.mark_dispatching(index=index, expected_revision=reserved.revision)
        result = await clients.credit_account(
            http,
            self.platform,
            agent.agent_id,
            amount,
            bank_url=self._config.bank_url,
            operation_id=operation_id,
            reference=reference,
        )
        if result["outcome"] == "legacy_observation":
            legacy = LegacyFinancialEnvelope.model_validate(result)
            raise RuntimeError(
                "Demo funding received a historical observation "
                f"(tx_id={legacy.observation.transaction.tx_id}, "
                f"observed_fact_sha256={legacy.observation.observed_fact_sha256}); "
                "no native operation confirmation was created"
            )
        envelope = NativeFinancialEnvelope.model_validate(result)
        store.finish_step(
            index=index,
            expected_revision=dispatched.revision,
            state="completed",
            result=result,
            diagnostic=None,
        )
        balance_after = envelope.receipt.result["balance_after"]
        if type(balance_after) is not int:
            raise RuntimeError("Funding receipt has an invalid balance_after")
        console.print(
            f"  [green]Funded {agent.name}[/green] +{amount} coins"
            f" (balance: {balance_after}, operation_id: {envelope.receipt.operation_id})"
        )

    async def _do_post_task(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        terms = posting_terms(step)
        poster_handle = required_text(step, "poster")
        poster = self.agents[poster_handle]
        sdk_poster: Any = poster
        store = self._require_store()
        index = self._step_index(step)
        existing = store.step(index)
        if existing is None:
            intent = sdk_poster.prepare_task_posting(
                required_text(step, "title"),
                required_text(step, "spec"),
                exact_integer(step.get("reward"), "reward", 1, SQLITE_MAX_INTEGER),
                terms["bidding_deadline_seconds"],
                terms["acceptance_window_seconds"],
                terms["execution_deadline_seconds"],
                terms["review_deadline_seconds"],
            )
            existing = store.reserve_posting(
                index=index,
                step=step,
                actor_handle=poster_handle,
                intent=intent,
            )
        if existing.state == "completed":
            assert existing.result is not None
            body = existing.result["body"]
            if not isinstance(body, dict):
                raise RuntimeError("Completed posting body is invalid")
            result = body
        else:
            if existing.state in {"attention_required", "definitive_rejection"}:
                raise RuntimeError("Task publication requires operator attention")
            if not isinstance(existing.intent, TaskPostingIntent):
                raise RuntimeError("Durable posting intent is invalid")
            dispatched = store.mark_dispatching(index=index, expected_revision=existing.revision)
            response = None
            try:
                status = await sdk_poster.get_task_posting(existing.intent.operation_id)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
            else:
                publication_state = status.body.get("publication_state")
                if publication_state in {"published", "blocked_integrity"}:
                    response = status
                elif publication_state != "awaiting_lock":
                    raise RuntimeError("Task publication requires operator attention")
            if response is None:
                response = await sdk_poster.submit_task_posting(existing.intent)
            completed = store.complete_posting(
                index=index,
                expected_revision=dispatched.revision,
                response=response,
            )
            if completed.state == "attention_required":
                raise RuntimeError("Task publication requires operator attention")
            if completed.state != "completed":
                raise RuntimeError("Task publication remains pending")
            assert completed.result is not None
            body = completed.result["body"]
            if not isinstance(body, dict):
                raise RuntimeError("Completed posting body is invalid")
            result = body
        task_id = required_text(result, "task_id")
        self._latest_task[poster_handle] = task_id
        if "ref" in step:
            self._refs[step["ref"]] = task_id
        console.print(
            f'  [green]Task posted:[/green] "{step["title"]}" for {step["reward"]} coins -> {task_id}'
        )

    async def _do_bid(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        intent = self._existing_intent(step, "bid")
        if intent is None:
            bidder = self.agents[required_text(step, "bidder")]
            if "task_ref" in step:
                task_id = self._refs[step["task_ref"]]
            elif "poster" in step:
                task_id = self._latest_task[step["poster"]]
            else:
                task_id = list(self._latest_task.values())[-1]
            amount = exact_integer(step.get("amount"), "amount", 1, SQLITE_MAX_INTEGER)
            intent = await self._remember_intent(
                http, step, "bid", bidder, task_id, {"amount": amount}
            )
        amount = intent.arguments()["amount"]
        dispatched = self._mark_native_dispatching(step)
        response = await clients.submit_bid(
            http,
            self._actor_for_intent(intent),
            intent.task_id,
            amount,
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        bid = completed_bid(intent, response)
        tracked = self._bids.setdefault(intent.task_id, [])
        if all(item["bid_id"] != bid["bid_id"] for item in tracked):
            tracked.append(bid)
        self._complete_native_step(
            step,
            dispatched,
            {"task_id": intent.task_id, "bid": bid},
        )
        console.print(
            f"  [green]{self._actor_for_intent(intent).name} bid {amount} coins[/green]"
            f" on task -> bid_id={bid['bid_id']}"
        )

    async def _do_accept_bid(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        intent = self._existing_intent(step, "accept_bid")
        if intent is None:
            poster_handle = required_text(step, "poster")
            bidder_handle = required_text(step, "bidder")
            poster, bidder = self.agents[poster_handle], self.agents[bidder_handle]
            task_id = self._resolve_task_id(step, poster_handle)
            bids = await clients.list_bids(http, poster, task_id, self._config.task_board_url)
            matches = [bid for bid in bids if bid.get("bidder_id") == bidder.agent_id]
            if len(matches) != 1 or bidder.agent_id is None:
                raise RuntimeError(f"Exactly one registered bid from {bidder_handle} is required")
            selected = matches[0]
            arguments: dict[str, Any] = {
                "bid_id": required_text(selected, "bid_id"),
                "worker_id": bidder.agent_id,
                "bidder_handle": bidder_handle,
                "bid_amount": exact_integer(
                    selected.get("amount"), "bid amount", 1, SQLITE_MAX_INTEGER
                ),
            }
            intent = await self._remember_intent(
                http, step, "accept_bid", poster, task_id, arguments
            )
        arguments = intent.arguments()
        dispatched = self._mark_native_dispatching(step)
        response = await clients.accept_bid(
            http,
            self._actor_for_intent(intent),
            intent.task_id,
            arguments["bid_id"],
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        completed_mutation(intent, response)
        bidder_handle = arguments["bidder_handle"]
        self._worker_task[bidder_handle] = intent.task_id
        self._complete_native_step(
            step,
            dispatched,
            {
                "task_id": intent.task_id,
                "bidder_handle": bidder_handle,
                "response": response,
            },
        )
        console.print(
            f"  [green]{self._actor_for_intent(intent).name} accepted {bidder_handle}'s bid[/green]"
            " -> contract formed"
        )

    async def _do_upload_asset(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        worker_handle = step["worker"]
        worker = self.agents[worker_handle]
        task_id = self._resolve_task_id(step, worker_handle)
        filename = step.get("filename", "deliverable.txt")
        content_str = step.get("content", "Demo deliverable content")
        content = content_str.encode() if isinstance(content_str, str) else content_str
        dispatched = self._begin_nonreplayable_step(
            step,
            actor_handle=worker_handle,
            intent={
                "task_id": task_id,
                "filename": filename,
                "content_sha256": hashlib.sha256(content).hexdigest(),
            },
        )
        result = await clients.upload_asset(
            http, worker, task_id, filename, content, self._config.task_board_url
        )
        self._complete_native_step(
            step,
            dispatched,
            {"task_id": task_id, "asset": result},
        )
        console.print(f"  [green]{worker.name} uploaded[/green] '{filename}'")

    async def _do_submit_deliverable(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        intent = self._existing_intent(step, "submit_deliverable")
        if intent is None:
            handle = required_text(step, "worker")
            task_id = self._resolve_task_id(step, handle)
            intent = await self._remember_intent(
                http, step, "submit_deliverable", self.agents[handle], task_id, {}
            )
        dispatched = self._mark_native_dispatching(step)
        response = await clients.submit_deliverable(
            http,
            self._actor_for_intent(intent),
            intent.task_id,
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        completed_mutation(intent, response)
        self._complete_native_step(
            step,
            dispatched,
            {"task_id": intent.task_id, "response": response},
        )
        console.print(
            f"  [green]{self._actor_for_intent(intent).name} submitted deliverables[/green]"
            " for review"
        )

    async def _do_approve(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        intent = self._existing_intent(step, "approve")
        if intent is None:
            handle = required_text(step, "poster")
            task_id = self._resolve_task_id(step, handle)
            intent = await self._remember_intent(
                http, step, "approve", self.agents[handle], task_id, {}
            )
        dispatched = self._mark_native_dispatching(step)
        response = await clients.approve_task(
            http,
            self._actor_for_intent(intent),
            intent.task_id,
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        completed_mutation(intent, response)
        self._complete_native_step(
            step,
            dispatched,
            {"task_id": intent.task_id, "response": response},
        )
        console.print(
            f"  [green]{self._actor_for_intent(intent).name} approved task[/green]"
            " -> payout released!"
        )

    async def _do_dispute(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        intent = self._existing_intent(step, "dispute")
        if intent is None:
            handle = required_text(step, "poster")
            task_id = self._resolve_task_id(step, handle)
            intent = await self._remember_intent(
                http,
                step,
                "dispute",
                self.agents[handle],
                task_id,
                {"reason": required_text(step, "reason")},
            )
        dispatched = self._mark_native_dispatching(step)
        response = await clients.dispute_task(
            http,
            self._actor_for_intent(intent),
            intent.task_id,
            intent.arguments()["reason"],
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        result, _current = completed_mutation(intent, response)
        dispute_id = required_text(result, "dispute_id")
        self._disputes[intent.task_id] = dispute_id
        self._complete_native_step(
            step,
            dispatched,
            {
                "task_id": intent.task_id,
                "dispute_id": dispute_id,
                "response": response,
            },
        )
        console.print(
            f"  [green]{self._actor_for_intent(intent).name} disputed task:[/green] "
            f'"{intent.arguments()["reason"]}"'
        )

    async def _resolve_dispute_id(self, http: httpx.AsyncClient, task_id: str) -> str:
        if task_id in self._disputes:
            return self._disputes[task_id]
        disputes = await clients.list_disputes(http, self._config.court_url, task_id=task_id)
        if not disputes:
            msg = f"No Court dispute found for task {task_id}"
            raise ValueError(msg)
        dispute_id = str(disputes[0]["dispute_id"])
        self._disputes[task_id] = dispute_id
        return dispute_id

    async def _do_file_claim(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        assert self.platform is not None
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        task_id = self._resolve_task_id(step, poster_handle)
        task = await clients.get_task(http, task_id, self._config.task_board_url)
        respondent_id = task.get("worker_id")
        if not isinstance(respondent_id, str) or respondent_id == "":
            worker_handle = step.get("worker")
            if not isinstance(worker_handle, str):
                msg = "file_claim requires an accepted task or explicit worker"
                raise ValueError(msg)
            worker = self.agents[worker_handle]
            if worker.agent_id is None:
                raise ValueError("file_claim worker must be registered")
            respondent_id = worker.agent_id

        claim = step.get("claim", step.get("reason", "Deliverable does not meet specification"))
        if not isinstance(claim, str) or not claim:
            raise ValueError("file_claim claim must be a nonempty string")
        escrow_id = required_text(task, "escrow_id")
        if poster.agent_id is None:
            raise ValueError("file_claim poster must be registered")
        intent = {
            "task_id": task_id,
            "claimant_id": poster.agent_id,
            "respondent_id": respondent_id,
            "claim": claim,
            "escrow_id": escrow_id,
        }
        store = self._require_store()
        index = self._step_index(step)
        current = store.reserve_step(
            index=index,
            step=step,
            actor_handle=poster_handle,
            intent=intent,
        )
        stored_intent = dict(cast("Mapping[str, object]", current.intent))
        if current.state == "dispatching":
            summaries = await clients.list_disputes(
                http, self._config.court_url, task_id=cast("str", stored_intent["task_id"])
            )
            matches: list[dict[str, Any]] = []
            for summary in summaries:
                candidate_id = summary.get("dispute_id")
                if not isinstance(candidate_id, str):
                    continue
                candidate = await clients.get_dispute(http, self._config.court_url, candidate_id)
                if all(candidate.get(field) == value for field, value in stored_intent.items()):
                    matches.append(candidate)
            if len(matches) == 1:
                result = matches[0]
                dispute_id = required_text(result, "dispute_id")
                store.finish_step(
                    index=index,
                    expected_revision=current.revision,
                    state="completed",
                    result={
                        "task_id": stored_intent["task_id"],
                        "dispute_id": dispute_id,
                        "response": result,
                    },
                    diagnostic=None,
                )
                self._disputes[cast("str", stored_intent["task_id"])] = dispute_id
                console.print(f"  [green]Court claim filed:[/green] {dispute_id}")
                return
            self._mark_attention(index, current, "ambiguous_claim")
            raise RuntimeError(f"Durable step {index} requires operator attention")
        if current.state in {"attention_required", "definitive_rejection"}:
            raise RuntimeError(f"Durable step {index} requires operator attention")
        current = store.mark_dispatching(index=index, expected_revision=current.revision)
        result = await clients.file_claim(
            http,
            self.platform,
            task_id=cast("str", stored_intent["task_id"]),
            claimant_id=cast("str", stored_intent["claimant_id"]),
            respondent_id=cast("str", stored_intent["respondent_id"]),
            claim=cast("str", stored_intent["claim"]),
            escrow_id=cast("str", stored_intent["escrow_id"]),
            court_url=self._config.court_url,
        )
        dispute_id = required_text(result, "dispute_id")
        store.finish_step(
            index=index,
            expected_revision=current.revision,
            state="completed",
            result={
                "task_id": stored_intent["task_id"],
                "dispute_id": dispute_id,
                "response": result,
            },
            diagnostic=None,
        )
        self._disputes[cast("str", stored_intent["task_id"])] = dispute_id
        console.print(f"  [green]Court claim filed:[/green] {dispute_id}")

    async def _do_submit_rebuttal(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        worker_handle = step["worker"]
        worker = self.agents[worker_handle]
        task_id = self._resolve_task_id(step, worker_handle)
        dispute_id = await self._resolve_dispute_id(http, task_id)
        rebuttal = step.get("rebuttal", "The deliverable meets the specification.")
        intent = {
            "task_id": task_id,
            "dispute_id": dispute_id,
            "rebuttal": str(rebuttal),
        }
        store = self._require_store()
        index = self._step_index(step)
        current = store.reserve_step(
            index=index,
            step=step,
            actor_handle=worker_handle,
            intent=intent,
        )
        stored_intent = dict(cast("Mapping[str, object]", current.intent))
        if current.state == "dispatching":
            observed = await clients.get_dispute(
                http, self._config.court_url, cast("str", stored_intent["dispute_id"])
            )
            if observed.get("rebuttal") == stored_intent["rebuttal"]:
                store.finish_step(
                    index=index,
                    expected_revision=current.revision,
                    state="completed",
                    result={
                        "task_id": stored_intent["task_id"],
                        "dispute_id": stored_intent["dispute_id"],
                        "response": observed,
                    },
                    diagnostic=None,
                )
                console.print(f"  [green]{worker.name} submitted rebuttal[/green] -> {dispute_id}")
                return
            self._mark_attention(index, current, "ambiguous_rebuttal")
            raise RuntimeError(f"Durable step {index} requires operator attention")
        if current.state in {"attention_required", "definitive_rejection"}:
            raise RuntimeError(f"Durable step {index} requires operator attention")
        current = store.mark_dispatching(index=index, expected_revision=current.revision)
        result = await clients.submit_rebuttal(
            http,
            worker,
            cast("str", stored_intent["task_id"]),
            cast("str", stored_intent["dispute_id"]),
            cast("str", stored_intent["rebuttal"]),
            self._config.task_board_url,
        )
        store.finish_step(
            index=index,
            expected_revision=current.revision,
            state="completed",
            result={
                "task_id": stored_intent["task_id"],
                "dispute_id": stored_intent["dispute_id"],
                "response": result,
            },
            diagnostic=None,
        )
        console.print(f"  [green]{worker.name} submitted rebuttal[/green] -> {dispute_id}")

    async def _do_trigger_ruling(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        assert self.platform is not None
        agent_handle = step.get("agent") or step.get("poster") or step.get("worker")
        if not isinstance(agent_handle, str):
            msg = "trigger_ruling requires agent, poster, worker, or task_ref context"
            raise ValueError(msg)
        task_id = self._resolve_task_id(step, agent_handle)
        dispute_id = await self._resolve_dispute_id(http, task_id)
        intent = {"task_id": task_id, "dispute_id": dispute_id}
        store = self._require_store()
        index = self._step_index(step)
        current = store.reserve_step(
            index=index,
            step=step,
            actor_handle=agent_handle,
            intent=intent,
        )
        stored_intent = dict(cast("Mapping[str, object]", current.intent))
        if current.state == "dispatching":
            observed = await clients.get_dispute(
                http, self._config.court_url, cast("str", stored_intent["dispute_id"])
            )
            if observed.get("status") == "ruled":
                store.finish_step(
                    index=index,
                    expected_revision=current.revision,
                    state="completed",
                    result={**stored_intent, "response": observed},
                    diagnostic=None,
                )
                result = observed
                console.print(
                    f"  [green]Court ruled[/green] dispute={dispute_id}"
                    f" worker_pct={result.get('worker_pct', '?')}"
                )
                return
            self._mark_attention(index, current, "ambiguous_ruling")
            raise RuntimeError(f"Durable step {index} requires operator attention")
        if current.state in {"attention_required", "definitive_rejection"}:
            raise RuntimeError(f"Durable step {index} requires operator attention")
        observed = await clients.get_dispute(
            http, self._config.court_url, cast("str", stored_intent["dispute_id"])
        )
        if observed.get("status") == "ruled":
            current = store.mark_dispatching(index=index, expected_revision=current.revision)
            store.finish_step(
                index=index,
                expected_revision=current.revision,
                state="completed",
                result={**stored_intent, "response": observed},
                diagnostic=None,
            )
            console.print(
                f"  [green]Court ruled[/green] dispute={dispute_id}"
                f" worker_pct={observed.get('worker_pct', '?')}"
            )
            return
        if observed.get("status") not in {"rebuttal_pending", "rebuttal_submitted"}:
            current = store.mark_dispatching(index=index, expected_revision=current.revision)
            self._mark_attention(index, current, "ambiguous_ruling")
            raise RuntimeError(f"Durable step {index} requires operator attention")
        current = store.mark_dispatching(index=index, expected_revision=current.revision)
        try:
            result = await clients.trigger_ruling(
                http,
                self.platform,
                cast("str", stored_intent["dispute_id"]),
                self._config.court_url,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            observed = await clients.get_dispute(
                http, self._config.court_url, cast("str", stored_intent["dispute_id"])
            )
            if observed.get("status") != "ruled":
                self._mark_attention(index, current, "ambiguous_ruling")
                raise RuntimeError(f"Durable step {index} requires operator attention") from exc
            result = observed
        store.finish_step(
            index=index,
            expected_revision=current.revision,
            state="completed",
            result={**stored_intent, "response": result},
            diagnostic=None,
        )
        console.print(
            f"  [green]Court ruled[/green] dispute={dispute_id}"
            f" worker_pct={result.get('worker_pct', '?')}"
        )

    async def _do_feedback(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        self._require_store()
        agent_handle = step["agent"]
        agent = self.agents[agent_handle]
        task_id = self._resolve_task_id(step, agent_handle)

        raw_to_agent_id = step["to_agent_id"]
        if raw_to_agent_id in self.agents:
            resolved_to_agent = self.agents[raw_to_agent_id]
            if resolved_to_agent.agent_id is None:
                msg = f"Agent '{raw_to_agent_id}' has no registered agent_id"
                raise ValueError(msg)
            to_agent_id = resolved_to_agent.agent_id
        else:
            to_agent_id = raw_to_agent_id

        comment = step.get("comment", "")
        if not isinstance(comment, str):
            raise ValueError("feedback comment must be a string")
        dispatched = self._begin_nonreplayable_step(
            step,
            actor_handle=agent_handle,
            intent={
                "task_id": task_id,
                "to_agent_id": to_agent_id,
                "category": required_text(step, "category"),
                "rating": required_text(step, "rating"),
                "comment": comment,
            },
        )
        result = await clients.submit_feedback(
            http,
            agent,
            task_id=task_id,
            to_agent_id=to_agent_id,
            category=step["category"],
            rating=step["rating"],
            comment=comment,
            reputation_url=self._config.reputation_url,
        )
        self._complete_native_step(
            step,
            dispatched,
            {"task_id": task_id, "feedback": result},
        )
        console.print(
            f"  [green]{agent.name} submitted {step['category']} feedback[/green]"
            f" ({step['rating']})"
        )
