"""CLI entry point for the demo replay engine.

Usage::

    cd tools/
    uv run python -m demo_replay scenarios/quick.yaml --run-id run-<uuid>
    uv run python -m demo_replay scenarios/scale.yaml --run-id run-<uuid> --delay 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import cast

from demo_replay.config import load_demo_settings, load_platform_settings
from demo_replay.engine import ReplayEngine, load_scenario


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a demo scenario against live services.",
    )
    parser.add_argument(
        "scenario",
        type=Path,
        help="Path to the YAML scenario file.",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Durable run identity in run-<uuid> form.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Override default delay between steps (seconds).",
    )
    return parser.parse_args()


def main() -> None:
    """Parse args, load scenario, run replay."""
    args = _parse_args()

    scenario_path: Path = args.scenario
    if not scenario_path.exists():
        print(f"Error: scenario file not found: {scenario_path}", file=sys.stderr)
        sys.exit(1)

    scenario_bytes = scenario_path.read_bytes()
    scenario = load_scenario(scenario_path)
    configured_delay = scenario.get("default_delay")
    effective_delay = args.delay if args.delay is not None else configured_delay
    if type(effective_delay) not in {int, float}:
        print("Error: scenario default_delay or --delay must be explicit", file=sys.stderr)
        sys.exit(1)
    effective_delay_number = cast("int | float", effective_delay)

    config = load_platform_settings()
    demo_config = load_demo_settings()
    engine = ReplayEngine(scenario, config)
    engine.bind_durable_run(
        run_id=args.run_id,
        scenario_bytes=scenario_bytes,
        demo_config=demo_config,
        effective_delay_seconds=float(effective_delay_number),
    )
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
