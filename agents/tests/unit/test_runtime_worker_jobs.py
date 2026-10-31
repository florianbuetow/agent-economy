"""Durable worker job records under the runtime identity lease."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from base_agent.runtime_intents import RuntimeIntentError
from tests.unit.math_worker.runtime_fixtures import TASK_ID, runtime_worker

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_job_record_survives_store_reopen(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (_agent, _store, commands):
        assert commands.jobs.load(TASK_ID) is None
        commands.jobs.save(TASK_ID, {"phase": "bid", "bid_amount": 40})
        commands.jobs.save(TASK_ID, {"phase": "accepted", "bid_amount": 40})

    async with runtime_worker(root) as (_agent, _store, commands):
        assert commands.jobs.load(TASK_ID) == {"phase": "accepted", "bid_amount": 40}
        assert commands.jobs.all() == ((TASK_ID, {"phase": "accepted", "bid_amount": 40}),)


async def test_job_staging_leftovers_are_removed_and_unknown_entries_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (_agent, store, commands):
        commands.jobs.save(TASK_ID, {"phase": "bid"})
        jobs_dir = store.identity_directory / "jobs"

    staging = jobs_dir / f".{'a' * 64}.json.{'b' * 24}.tmp"
    staging.write_bytes(b"{}")
    staging.chmod(0o600)
    async with runtime_worker(root) as (_agent, _store, commands):
        assert not staging.exists()
        assert commands.jobs.load(TASK_ID) == {"phase": "bid"}

    (jobs_dir / "stray.txt").write_bytes(b"x")
    with pytest.raises(RuntimeIntentError) as raised:
        async with runtime_worker(root):
            pass
    assert raised.value.reason == "corrupt_record"


async def test_job_record_rejects_noncanonical_task_id(tmp_path: Path) -> None:
    async with runtime_worker(tmp_path / "runtime") as (_agent, _store, commands):
        with pytest.raises(RuntimeIntentError):
            commands.jobs.save("../escape", {"phase": "bid"})
