"""Unit tests for the Task Feeder acceptance configuration (WP-15 / Q-16 / T-035).

The acceptance keys live under ``task_feeder`` in ``config.yaml`` but are loaded
into a dedicated ``AcceptanceConfig`` model so the existing ``TaskFeederConfig``
surface (and its frozen tests) is untouched.  ``acceptance_after_seconds`` must be
strictly less than ``bidding_deadline_seconds`` or every task would race its own
bidding-deadline expiry (T-035).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from task_feeder.config import (
    AcceptanceConfig,
    load_acceptance_settings,
    load_task_feeder_settings,
)

if TYPE_CHECKING:
    from pathlib import Path

VALID_CONFIG_YAML = """\
task_feeder:
  handle: "feeder"
  tasks_file: "../data/math_tasks.jsonl"
  feed_interval_seconds: 15
  max_open_tasks: 5
  bidding_deadline_seconds: 120
  execution_deadline_seconds: 300
  review_deadline_seconds: 120
  review_interval_seconds: 30
  auto_approve_on_error: false
  base_reward: 10
  reward_per_level: 10
  shuffle: true
  acceptance_after_seconds: 30
  acceptance_poll_interval_seconds: 5
  min_bids_to_accept: 2
"""


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


@pytest.mark.unit
class TestAcceptanceConfig:
    def test_creates_config(self) -> None:
        config = AcceptanceConfig(
            acceptance_after_seconds=30,
            acceptance_poll_interval_seconds=5,
            min_bids_to_accept=2,
            bidding_deadline_seconds=120,
        )
        assert config.acceptance_after_seconds == 30
        assert config.acceptance_poll_interval_seconds == 5
        assert config.min_bids_to_accept == 2
        assert config.bidding_deadline_seconds == 120

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            AcceptanceConfig(
                acceptance_after_seconds=30,
                acceptance_poll_interval_seconds=5,
                min_bids_to_accept=2,
                bidding_deadline_seconds=120,
                extra_field="bad",  # type: ignore[call-arg]
            )

    def test_acceptance_after_must_be_strictly_less_than_bidding_deadline(self) -> None:
        with pytest.raises(ValidationError):
            AcceptanceConfig(
                acceptance_after_seconds=200,
                acceptance_poll_interval_seconds=5,
                min_bids_to_accept=2,
                bidding_deadline_seconds=120,
            )

    def test_acceptance_after_equal_to_deadline_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AcceptanceConfig(
                acceptance_after_seconds=120,
                acceptance_poll_interval_seconds=5,
                min_bids_to_accept=2,
                bidding_deadline_seconds=120,
            )


@pytest.mark.unit
class TestLoadAcceptanceSettings:
    def test_loads_acceptance_keys_from_yaml(self, tmp_path: Path) -> None:
        path = _write_config(tmp_path, VALID_CONFIG_YAML)
        config = load_acceptance_settings(path)
        assert config.acceptance_after_seconds == 30
        assert config.acceptance_poll_interval_seconds == 5
        assert config.min_bids_to_accept == 2
        assert config.bidding_deadline_seconds == 120

    def test_missing_acceptance_key_fails_fast(self, tmp_path: Path) -> None:
        body = VALID_CONFIG_YAML.replace("  min_bids_to_accept: 2\n", "")
        path = _write_config(tmp_path, body)
        with pytest.raises(ValidationError):
            load_acceptance_settings(path)

    def test_bad_ordering_in_yaml_fails_fast(self, tmp_path: Path) -> None:
        body = VALID_CONFIG_YAML.replace(
            "  acceptance_after_seconds: 30\n",
            "  acceptance_after_seconds: 130\n",
        )
        path = _write_config(tmp_path, body)
        with pytest.raises(ValidationError):
            load_acceptance_settings(path)

    def test_feeder_loader_tolerates_acceptance_keys(self, tmp_path: Path) -> None:
        # The acceptance keys share the ``task_feeder`` section; the feeder loader
        # must ignore them rather than reject them via ``extra="forbid"``.
        path = _write_config(tmp_path, VALID_CONFIG_YAML)
        feeder = load_task_feeder_settings(path)
        assert feeder.handle == "feeder"
        assert feeder.bidding_deadline_seconds == 120
