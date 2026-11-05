"""Literal process-crash recovery while a Court judging lease is held."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from db_gateway_service import task_time

from court_service.core.state import get_app_state
from tests.integration.test_ruling_durability import (
    _CountingJudge,
    _GatewayTaskBoard,
    _read,
    _rebutted_dispute,
    _rule,
    _wire,
)

if TYPE_CHECKING:
    import httpx

pytestmark = pytest.mark.integration

_CRASH_SCRIPT = """
import asyncio
import os
import signal
import sys
from pathlib import Path
from service_auth.gateway import GatewayRequestSigner
from court_service.config import get_config_path, get_settings
from court_service.judges import MockJudge
from court_service.services.dispute_db_client import DisputeDbClient
from court_service.services.ruling_orchestrator import RulingOrchestrator

settings = get_settings()
assert settings.db_gateway is not None
config_dir = Path(get_config_path()).parent
signer = GatewayRequestSigner(settings.db_gateway.signing, config_dir=config_dir)
store = DisputeDbClient(
    base_url=settings.db_gateway.url,
    timeout_seconds=settings.db_gateway.timeout_seconds,
    signer=signer,
)
orchestrator = RulingOrchestrator(
    store=store,
    feedback_extremely_satisfied_cutoff=80,
    feedback_satisfied_cutoff=40,
    feedback_comment_max_length=256,
    judging_lease_seconds=60,
)
claim = orchestrator.begin_ruling(sys.argv[1])
if sys.argv[2] == 'postvotes':
    asyncio.run(orchestrator._decide(
        sys.argv[1],
        claim,
        [MockJudge('judge-0', 70, 'satisfied', 'satisfied', 'Test')],
        {
            'title': 'Task',
            'spec': 'Return exact work.',
            'deliverables': [],
            'reward': 100,
            'lifecycle_version': 3,
        },
    ))
os.write(int(sys.argv[3]), b'1')
signal.pause()
"""


@pytest.mark.parametrize(("cut", "expected_judge_calls"), [("predecision", 1), ("postvotes", 0)])
async def test_new_process_reclaims_lease_left_by_crashed_judging_process(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
    expected_judge_calls: int,
) -> None:
    """SIGKILL before or after votes cannot strand or duplicate a ruling."""
    dispute_id = await _rebutted_dispute(client, gateway_client, gateway_db_path)
    read_fd, write_fd = os.pipe()
    crashed = subprocess.Popen(
        [sys.executable, "-c", _CRASH_SCRIPT, dispute_id, cut, str(write_fd)],
        cwd=Path.cwd(),
        env=os.environ.copy(),
        pass_fds=(write_fd,),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    os.close(write_fd)
    stderr = ""
    try:
        ready = await asyncio.wait_for(asyncio.to_thread(os.read, read_fd, 1), timeout=5)
        assert ready == b"1"
        crashed.kill()
        _, stderr = crashed.communicate(timeout=5)
    finally:
        os.close(read_fd)
        if crashed.poll() is None:
            crashed.kill()
            _, cleanup_stderr = crashed.communicate(timeout=5)
            stderr = stderr or cleanup_stderr
    assert crashed.returncode == -9, stderr

    state = get_app_state()
    task_board = _GatewayTaskBoard(gateway_client, gateway_db_path)
    judge = _CountingJudge(worker_pct=70, delay_seconds=0)
    _wire(state, task_board, judge)

    held = await _rule(client, dispute_id)
    assert held.status_code == 409

    later = task_time.utc_now_us() + 61 * 1_000_000
    monkeypatch.setattr(task_time, "utc_now_us", lambda: later)
    reclaimed = await _rule(client, dispute_id)

    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["status"] == "ruled"
    assert judge.calls == expected_judge_calls
    assert len(task_board.settlements) == 1
    rulings = _read(
        gateway_db_path,
        "SELECT ruling_id FROM court_rulings WHERE claim_id=?",
        (dispute_id,),
    )
    assert len(rulings) == 1
