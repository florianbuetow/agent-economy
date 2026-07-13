"""Unit tests for the math_worker CLI entry point (WP-09 item 3).

The legacy flat-config path silently bypassed the ``workers:`` profiles
in config.yaml (and its hardcoded ``api_key: "lm-studio"``). The entry
point must always go through ``WorkerFactory`` and fail loudly — not
silently fall back — when no profile name is given.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from math_worker import __main__ as math_worker_main


@pytest.mark.unit
class TestMainEntryPointRequiresProfile:
    def test_main_without_profile_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["math_worker"])
        with pytest.raises(SystemExit):
            math_worker_main.main()

    def test_main_with_profile_dispatches_to_factory_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["math_worker", "mathbot"])
        sentinel = object()
        with (
            patch.object(
                math_worker_main, "_main_factory", new=MagicMock(return_value=sentinel)
            ) as mock_factory,
            patch.object(math_worker_main.asyncio, "run") as mock_run,
        ):
            math_worker_main.main()

        mock_factory.assert_called_once_with("mathbot")
        mock_run.assert_called_once_with(sentinel)

    def test_no_legacy_entry_point_exists(self) -> None:
        assert not hasattr(math_worker_main, "_main_legacy")

    def test_load_math_worker_settings_no_longer_imported(self) -> None:
        assert not hasattr(math_worker_main, "load_math_worker_settings")
