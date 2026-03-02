"""Scenario engine — loads YAML, executes steps sequentially with delays."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import yaml
from rich.console import Console
from rich.panel import Panel

from demo_replay import clients
from demo_replay.wallet import DemoAgent

console = Console()


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
    return raw


class ReplayEngine:
    """Execute a demo scenario against live services."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
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
            self.agents[handle] = DemoAgent.create(handle, display_name)

        # Load the real platform agent (must match keys known to Central Bank)
        platform_key = Path(__file__).resolve().parents[3] / "data" / "keys" / "platform.key"
        self.platform = DemoAgent.from_pem("platform", "Platform", platform_key)

        async with httpx.AsyncClient(timeout=30.0) as http:
            # Register platform agent first
            console.print("[dim]Registering platform agent...[/dim]")
            await clients.register_agent(http, self.platform)
            console.print(
                f"  [green]Platform registered:[/green] {self.platform.agent_id}"
            )

            total = len(self.scenario["steps"])
            for i, step in enumerate(self.scenario["steps"], 1):
                action = step["action"]
                delay = float(step.get("delay", self.default_delay))

                console.print(
                    f"\n[bold][{i}/{total}][/bold] [yellow]{action}[/yellow]"
                )
                await self._execute_step(http, step)
                console.print(f"  [dim]waiting {delay}s...[/dim]")
                await asyncio.sleep(delay)

        console.print(Panel("[bold green]Demo complete![/bold green]"))

    async def _execute_step(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
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
            case "feedback":
                await self._do_feedback(http, step)
            case "reveal_feedback":
                await self._do_reveal_feedback(http, step)
            case _:
                console.print(f"  [red]Unknown action: {action}[/red]")

    async def _do_register(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        handle = step["agent"]
        agent = self.agents[handle]
        result = await clients.register_agent(http, agent)
        console.print(
            f"  [green]Registered {agent.name}[/green] -> {result['agent_id']}"
        )

        # Also create bank account via platform agent
        assert self.platform is not None
        assert agent.agent_id is not None
        await clients.create_account(http, self.platform, agent.agent_id)
        console.print(f"  [green]Bank account created[/green] for {agent.name}")

    async def _do_fund(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        handle = step["agent"]
        amount = int(step["amount"])
        agent = self.agents[handle]
        assert self.platform is not None
        assert agent.agent_id is not None
        result = await clients.credit_account(
            http, self.platform, agent.agent_id, amount
        )
        console.print(
            f"  [green]Funded {agent.name}[/green] +{amount} coins"
            f" (balance: {result.get('balance_after', '?')})"
        )

    async def _do_post_task(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        result = await clients.post_task(
            http,
            poster,
            title=step["title"],
            spec=step.get("spec", step["title"]),
            reward=int(step["reward"]),
        )
        task_id = result["task_id"]
        self._latest_task[poster_handle] = task_id
        if "ref" in step:
            self._refs[step["ref"]] = task_id
        console.print(
            f"  [green]Task posted:[/green]"
            f" \"{step['title']}\" for {step['reward']} coins -> {task_id}"
        )

    async def _do_bid(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        bidder_handle = step["bidder"]
        bidder = self.agents[bidder_handle]
        # Resolve task: check task_ref, then poster, then any latest task
        if "task_ref" in step:
            task_id = self._refs[step["task_ref"]]
        elif "poster" in step:
            task_id = self._latest_task[step["poster"]]
        else:
            # Find most recent task from any poster
            task_id = list(self._latest_task.values())[-1]
        amount = int(step["amount"])
        result = await clients.submit_bid(http, bidder, task_id, amount)
        bid_id = result.get("bid_id", "?")
        self._bids.setdefault(task_id, []).append(result)
        console.print(
            f"  [green]{bidder.name} bid {amount} coins[/green]"
            f" on task -> bid_id={bid_id}"
        )

    async def _do_accept_bid(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        bidder_handle = step["bidder"]
        task_id = self._resolve_task_id(step, poster_handle)

        # Find the bid_id for this bidder
        bids = await clients.list_bids(http, poster, task_id)
        bidder = self.agents[bidder_handle]
        bid_id: str | None = None
        for bid in bids:
            if bid.get("bidder_id") == bidder.agent_id:
                bid_id = bid["bid_id"]
                break
        if bid_id is None:
            console.print(f"  [red]No bid found from {bidder_handle}[/red]")
            return

        await clients.accept_bid(http, poster, task_id, bid_id)
        # Track which task this worker is assigned to
        self._worker_task[bidder_handle] = task_id
        console.print(
            f"  [green]{poster.name} accepted {bidder.name}'s bid[/green]"
            " -> contract formed"
        )

    async def _do_upload_asset(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        worker_handle = step["worker"]
        worker = self.agents[worker_handle]
        task_id = self._resolve_task_id(step, worker_handle)
        filename = step.get("filename", "deliverable.txt")
        content_str = step.get("content", "Demo deliverable content")
        content = content_str.encode() if isinstance(content_str, str) else content_str
        await clients.upload_asset(http, worker, task_id, filename, content)
        console.print(f"  [green]{worker.name} uploaded[/green] '{filename}'")

    async def _do_submit_deliverable(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        worker_handle = step["worker"]
        worker = self.agents[worker_handle]
        task_id = self._resolve_task_id(step, worker_handle)
        await clients.submit_deliverable(http, worker, task_id)
        console.print(
            f"  [green]{worker.name} submitted deliverables[/green] for review"
        )

    async def _do_approve(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        task_id = self._resolve_task_id(step, poster_handle)
        await clients.approve_task(http, poster, task_id)
        console.print(
            f"  [green]{poster.name} approved task[/green] -> payout released!"
        )

    async def _do_dispute(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        poster_handle = step["poster"]
        poster = self.agents[poster_handle]
        task_id = self._resolve_task_id(step, poster_handle)
        reason = step.get("reason", "Deliverable does not meet specification")
        await clients.dispute_task(http, poster, task_id, reason)
        console.print(
            f"  [green]{poster.name} disputed task:[/green] \"{reason}\""
        )

    async def _do_feedback(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
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
            role=step["role"],
            category=step["category"],
            rating=step["rating"],
            comment=step.get("comment", ""),
        )
        console.print(
            f"  [green]{agent.name} submitted {step['category']} feedback[/green]"
            f" ({step['rating']})"
        )

    async def _do_reveal_feedback(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        agent_handle = step["agent"]
        agent = self.agents[agent_handle]
        task_id = self._resolve_task_id(step, agent_handle)
        await clients.reveal_feedback(http, agent, task_id)
        console.print(
            f"  [green]{agent.name} revealed feedback[/green] for task"
        )
