"""Print the measured economy report.

Usage::

    cd agents/
    uv run python -m economy_report
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
from service_commons.config import get_config_path as resolve_config_path

from economy_report.config import load_report_settings
from economy_report.report import collect_workers, fetch_quality, render, summarize

if TYPE_CHECKING:
    from pathlib import Path


async def build_report(config_path: Path, client: httpx.AsyncClient) -> str:
    """Collect journals, summarize each configured worker and fetch its quality."""
    settings = load_report_settings(config_path)
    z = settings.report.confidence_z
    summaries = []
    for journal in collect_workers(settings.runtime_directory, roles=settings.report.worker_roles):
        pricing = settings.pricing.get(journal.profile)
        if pricing is None:
            raise KeyError(f"No classification_workers profile {journal.profile!r} in config")
        summaries.append(summarize(journal, rule=pricing.to_rule(), z=z))
    qualities = {
        s.agent_id: await fetch_quality(client, settings.reputation_url, s.agent_id)
        for s in summaries
    }
    return render(summaries, qualities, z=z)


async def _main() -> None:
    config_path = resolve_config_path(
        env_var_name="AGENT_CONFIG_PATH", default_filename="config.yaml"
    )
    settings = load_report_settings(config_path)
    async with httpx.AsyncClient(timeout=settings.report.quality_timeout_seconds) as client:
        print(await build_report(config_path, client))


def main() -> None:
    """Sync entry point."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
