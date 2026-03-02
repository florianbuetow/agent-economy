"""Puppet Master — orchestrates scenario execution.

Reads a YAML choreography file, instantiates puppet agents, and executes
steps sequentially. Wraps ReplayEngine with better error handling and
validation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from demo_replay.engine import ReplayEngine, load_scenario

console = Console()


class ScenarioValidationError(Exception):
    """Raised when a scenario file has structural problems."""


def validate_scenario(scenario: dict[str, Any]) -> list[str]:
    """Validate scenario structure and return list of warnings.

    Raises ScenarioValidationError for fatal issues.
    """
    warnings: list[str] = []
    agents = {a["handle"] for a in scenario.get("agents", [])}

    for i, step in enumerate(scenario.get("steps", []), 1):
        action = step.get("action")
        if not action:
            msg = f"Step {i}: missing 'action' field"
            raise ScenarioValidationError(msg)

        # Check agent references exist
        for field in ("agent", "poster", "bidder", "worker"):
            if field in step and step[field] not in agents:
                msg = f"Step {i}: unknown agent '{step[field]}' (not in agents list)"
                raise ScenarioValidationError(msg)

    return warnings


class PuppetMaster:
    """High-level orchestrator for scenario execution."""

    def __init__(self, scenario_path: Path) -> None:
        self.scenario_path = scenario_path
        self.scenario = load_scenario(scenario_path)
        self._engine: ReplayEngine | None = None

    def validate(self) -> list[str]:
        """Validate the scenario file. Returns warnings."""
        return validate_scenario(self.scenario)

    async def run(self) -> None:
        """Execute the scenario."""
        warnings = self.validate()
        for w in warnings:
            console.print(f"  [yellow]Warning: {w}[/yellow]")

        self._engine = ReplayEngine(self.scenario)
        await self._engine.run()
