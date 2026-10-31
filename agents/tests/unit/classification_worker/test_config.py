"""Shipped configuration for classification workers and the classification feeder workload."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from classification_worker.config import load_classification_profile
from task_feeder.config import load_task_feeder_settings
from task_feeder.reader import load_task_snapshot
from task_feeder.workloads.classification import (
    is_classification_task,
    load_seed,
    write_task_source,
)

AGENTS_DIR = Path(__file__).resolve().parents[3]
CONFIG = AGENTS_DIR / "config.yaml"
SEED = AGENTS_DIR / "src" / "task_feeder" / "workloads" / "classification_seed.json"


def _raw() -> dict[str, object]:
    return yaml.safe_load(CONFIG.read_text())


@pytest.mark.unit
class TestShippedProfiles:
    def test_at_least_two_profiles_with_different_strategies_and_prices(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LMSTUDIO_API_KEY", "test-key")
        names = list(_raw()["classification_workers"])  # type: ignore[call-overload]
        profiles = [load_classification_profile(CONFIG, name) for name in names]
        assert len(profiles) >= 2
        assert len({p.strategy for p in profiles}) >= 2
        assert len({p.pricing.initial_bid_pct for p in profiles}) >= 2

    def test_profile_handles_are_in_the_roster(self) -> None:
        roster = yaml.safe_load((AGENTS_DIR / "roster.yaml").read_text())["agents"]
        for profile in _raw()["classification_workers"].values():  # type: ignore[union-attr]
            assert profile["handle"] in roster

    def test_keyword_profile_needs_no_llm_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
        profile = load_classification_profile(CONFIG, "classbot_keyword")
        assert profile.strategy == "keyword"
        assert profile.llm is None
        assert profile.keywords

    def test_unknown_profile_fails_fast(self) -> None:
        with pytest.raises(KeyError, match="nope"):
            load_classification_profile(CONFIG, "nope")

    def test_llm_strategy_requires_llm_section(self, tmp_path: Path) -> None:
        raw = _raw()
        profile = dict(raw["classification_workers"]["classbot_keyword"])  # type: ignore[index]
        profile["strategy"] = "llm"
        raw["classification_workers"] = {"broken": profile}
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(raw))
        with pytest.raises(ValueError, match="llm"):
            load_classification_profile(path, "broken")


@pytest.mark.unit
def test_feeder_switches_to_classification_by_config_alone(tmp_path: Path) -> None:
    source = tmp_path / "classification_tasks.jsonl"
    write_task_source(load_seed(SEED), source)
    raw = _raw()
    raw["task_feeder"]["tasks_file"] = str(source)  # type: ignore[index]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    settings = load_task_feeder_settings(path)
    snapshot = load_task_snapshot(Path(settings.tasks_file), max_bytes=settings.source_max_bytes)
    assert all(is_classification_task(entry.raw_task) for entry in snapshot.entries)
