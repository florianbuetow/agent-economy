"""Scenario engine — loads YAML, executes steps sequentially with delays."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    from demo_replay.config import PlatformConfig

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
            intent.verify_actor()
        return intent

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
            actor=actor,
            actor_id=actor.agent_id,
            actor_public_key=actor.public_key_string(),
            task_id=task_id,
            arguments_json=canonical_json(arguments),
            observed_task_json=canonical_json(observed),
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
        name = self.scenario["name"]
        console.print(Panel(f"[bold cyan]{name}[/bold cyan]", title="Demo Replay"))

        # Create agent objects (keypairs generated in memory)
        for agent_def in self.scenario["agents"]:
            handle = agent_def["handle"]
            display_name = agent_def["name"]
            self.agents[handle] = DemoAgent.create(
                handle, display_name, self._config.token_ttl_seconds
            )

        # Load the real platform agent (must match keys known to Central Bank)
        self.platform = DemoAgent.from_pem(
            "platform",
            "Platform",
            self._config.platform_key_path,
            self._config.token_ttl_seconds,
        )

        async with httpx.AsyncClient(timeout=30.0) as http:
            # Register platform agent first
            console.print("[dim]Registering platform agent...[/dim]")
            await clients.register_agent(http, self.platform, self._config.identity_url)
            console.print(f"  [green]Platform registered:[/green] {self.platform.agent_id}")

            total = len(self.scenario["steps"])
            for i, step in enumerate(self.scenario["steps"], 1):
                action = step["action"]
                delay = float(step.get("delay", self.default_delay))

                console.print(f"\n[bold][{i}/{total}][/bold] [yellow]{action}[/yellow]")
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
            original.verify_actor()
        self._active_step_index = step_index
        try:
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
        handle = step["agent"]
        agent = self.agents[handle]
        result = await clients.register_agent(http, agent, self._config.identity_url)
        console.print(f"  [green]Registered {agent.name}[/green] -> {result['agent_id']}")

        # Also create bank account via platform agent
        assert self.platform is not None
        assert agent.agent_id is not None
        await clients.create_account(http, self.platform, agent.agent_id, self._config.bank_url)
        console.print(f"  [green]Bank account created[/green] for {agent.name}")

    async def _do_fund(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
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
        balance_after = envelope.receipt.result["balance_after"]
        if type(balance_after) is not int:
            raise RuntimeError("Funding receipt has an invalid balance_after")
        console.print(
            f"  [green]Funded {agent.name}[/green] +{amount} coins"
            f" (balance: {balance_after}, operation_id: {envelope.receipt.operation_id})"
        )

    async def _do_post_task(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        terms = posting_terms(step)
        poster_handle = required_text(step, "poster")
        poster = self.agents[poster_handle]
        result = await clients.post_task(
            http,
            poster,
            title=required_text(step, "title"),
            spec=required_text(step, "spec"),
            reward=exact_integer(step.get("reward"), "reward", 1, SQLITE_MAX_INTEGER),
            task_board_url=self._config.task_board_url,
            **terms,
        )
        task_id = required_text(result, "task_id")
        self._latest_task[poster_handle] = task_id
        if "ref" in step:
            self._refs[step["ref"]] = task_id
        console.print(
            f'  [green]Task posted:[/green] "{step["title"]}" for {step["reward"]} coins -> {task_id}'
        )

    async def _do_bid(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
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
        response = await clients.submit_bid(
            http,
            intent.actor,
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
        console.print(
            f"  [green]{intent.actor.name} bid {amount} coins[/green] on task -> bid_id={bid['bid_id']}"
        )

    async def _do_accept_bid(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
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
        response = await clients.accept_bid(
            http,
            intent.actor,
            intent.task_id,
            arguments["bid_id"],
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        completed_mutation(intent, response)
        bidder_handle = arguments["bidder_handle"]
        self._worker_task[bidder_handle] = intent.task_id
        console.print(
            f"  [green]{intent.actor.name} accepted {bidder_handle}'s bid[/green] -> contract formed"
        )

    async def _do_upload_asset(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        worker_handle = step["worker"]
        worker = self.agents[worker_handle]
        task_id = self._resolve_task_id(step, worker_handle)
        filename = step.get("filename", "deliverable.txt")
        content_str = step.get("content", "Demo deliverable content")
        content = content_str.encode() if isinstance(content_str, str) else content_str
        await clients.upload_asset(
            http, worker, task_id, filename, content, self._config.task_board_url
        )
        console.print(f"  [green]{worker.name} uploaded[/green] '{filename}'")

    async def _do_submit_deliverable(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        intent = self._existing_intent(step, "submit_deliverable")
        if intent is None:
            handle = required_text(step, "worker")
            task_id = self._resolve_task_id(step, handle)
            intent = await self._remember_intent(
                http, step, "submit_deliverable", self.agents[handle], task_id, {}
            )
        response = await clients.submit_deliverable(
            http,
            intent.actor,
            intent.task_id,
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        completed_mutation(intent, response)
        console.print(f"  [green]{intent.actor.name} submitted deliverables[/green] for review")

    async def _do_approve(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        intent = self._existing_intent(step, "approve")
        if intent is None:
            handle = required_text(step, "poster")
            task_id = self._resolve_task_id(step, handle)
            intent = await self._remember_intent(
                http, step, "approve", self.agents[handle], task_id, {}
            )
        response = await clients.approve_task(
            http,
            intent.actor,
            intent.task_id,
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        completed_mutation(intent, response)
        console.print(f"  [green]{intent.actor.name} approved task[/green] -> payout released!")

    async def _do_dispute(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
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
        response = await clients.dispute_task(
            http,
            intent.actor,
            intent.task_id,
            intent.arguments()["reason"],
            self._config.task_board_url,
            operation_id=intent.operation_id,
            expected_lifecycle_version=intent.expected_lifecycle_version,
        )
        result, _current = completed_mutation(intent, response)
        self._disputes[intent.task_id] = required_text(result, "dispute_id")
        console.print(
            f'  [green]{intent.actor.name} disputed task:[/green] "{intent.arguments()["reason"]}"'
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
            respondent_id = str(worker.agent_id)

        claim = step.get("claim", step.get("reason", "Deliverable does not meet specification"))
        result = await clients.file_claim(
            http,
            self.platform,
            task_id=task_id,
            claimant_id=str(poster.agent_id),
            respondent_id=respondent_id,
            claim=str(claim),
            escrow_id=str(task["escrow_id"]),
            court_url=self._config.court_url,
        )
        dispute_id = str(result["dispute_id"])
        self._disputes[task_id] = dispute_id
        console.print(f"  [green]Court claim filed:[/green] {dispute_id}")

    async def _do_submit_rebuttal(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        worker_handle = step["worker"]
        worker = self.agents[worker_handle]
        task_id = self._resolve_task_id(step, worker_handle)
        dispute_id = await self._resolve_dispute_id(http, task_id)
        rebuttal = step.get("rebuttal", "The deliverable meets the specification.")
        await clients.submit_rebuttal(
            http,
            worker,
            task_id,
            dispute_id,
            str(rebuttal),
            self._config.task_board_url,
        )
        console.print(f"  [green]{worker.name} submitted rebuttal[/green] -> {dispute_id}")

    async def _do_trigger_ruling(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
        assert self.platform is not None
        agent_handle = step.get("agent") or step.get("poster") or step.get("worker")
        if not isinstance(agent_handle, str):
            msg = "trigger_ruling requires agent, poster, worker, or task_ref context"
            raise ValueError(msg)
        task_id = self._resolve_task_id(step, agent_handle)
        dispute_id = await self._resolve_dispute_id(http, task_id)
        result = await clients.trigger_ruling(
            http, self.platform, dispute_id, self._config.court_url
        )
        console.print(
            f"  [green]Court ruled[/green] dispute={dispute_id}"
            f" worker_pct={result.get('worker_pct', '?')}"
        )

    async def _do_feedback(self, http: httpx.AsyncClient, step: dict[str, Any]) -> None:
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

        await clients.submit_feedback(
            http,
            agent,
            task_id=task_id,
            to_agent_id=to_agent_id,
            category=step["category"],
            rating=step["rating"],
            comment=step.get("comment", ""),
            reputation_url=self._config.reputation_url,
        )
        console.print(
            f"  [green]{agent.name} submitted {step['category']} feedback[/green]"
            f" ({step['rating']})"
        )
