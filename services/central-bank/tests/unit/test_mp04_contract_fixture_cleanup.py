"""The real Gateway contract fixture releases its config when a test fails."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.unit.test_ledger_store_contract import _gateway_backed_client

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_gateway_contract_config_is_removed_after_a_body_exception(tmp_path: Path) -> None:
    previous_config = os.environ.get("CONFIG_PATH")
    fixture = _gateway_backed_client(str(tmp_path / "contract.db"))
    config_path: Path | None = None
    try:
        await anext(fixture)
        config_path = Path(os.environ["CONFIG_PATH"])
        assert config_path.is_file()
        with pytest.raises(RuntimeError, match="injected contract test failure"):
            await fixture.athrow(RuntimeError("injected contract test failure"))
        assert os.environ.get("CONFIG_PATH") == previous_config
        assert not config_path.exists()
    finally:
        await fixture.aclose()
        if config_path is not None:
            config_path.unlink(missing_ok=True)
