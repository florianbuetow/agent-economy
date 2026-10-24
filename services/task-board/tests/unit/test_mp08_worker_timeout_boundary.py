"""Recovery imports must not depend on a client's incidental dependency namespace."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from task_board_service.services import task_db_client

pytestmark = pytest.mark.unit


def test_recovery_loads_using_public_client_boundary_only(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(task_db_client.__file__).with_name("financial_effect_recovery.py")
    spec = importlib.util.spec_from_file_location("mp08_recovery_boundary_probe", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A boundary's internal import name is not part of its public type contract.
    # Existing exported exception aliases retain their identities without it.
    monkeypatch.delattr(task_db_client, "httpx")
    spec.loader.exec_module(module)
    assert callable(module.FinancialEffectRecovery)
