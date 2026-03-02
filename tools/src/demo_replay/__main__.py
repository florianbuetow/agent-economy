"""CLI entry point for the demo replay engine.

Usage::

    cd tools/
    uv run python -m demo_replay scenarios/quick.yaml
    uv run python -m demo_replay scenarios/scale.yaml --delay 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

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

    scenario = load_scenario(scenario_path)

    if args.delay is not None:
        scenario["default_delay"] = args.delay

    engine = ReplayEngine(scenario)
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
