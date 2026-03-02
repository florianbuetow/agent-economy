"""WorkerFactory — creates fully-wired math worker instances from named profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from service_commons.config import get_config_path as resolve_config_path

from base_agent.factory import AgentFactory
from base_agent.worker_config import WorkerProfile
from math_worker.config import LLMConfig, MathWorkerConfig
from math_worker.llm_client import LLMClient
from math_worker.loop import MathWorkerLoop

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


@dataclass(frozen=True)
class MathWorkerBundle:
    """Everything needed to run a math worker — agent, LLM client, and loop."""

    agent: BaseAgent
    llm: LLMClient
    loop: MathWorkerLoop


class WorkerFactory:
    """Factory that creates fully-wired worker instances from named profiles.

    Reads the ``workers`` section from config.yaml. Each worker profile
    specifies a roster handle, LLM configuration, and behavior tuning.
    The factory validates that each handle exists in the roster at
    construction time — fail fast, no defaults.

    Args:
        config_path: Path to config.yaml. Resolved via AGENT_CONFIG_PATH
            env var or default location if None.
        keys_dir: Override for the keys directory. If None, resolved
            from config.yaml's data.keys_dir.
    """

    def __init__(
        self,
        config_path: Path | None = None,
        keys_dir: Path | None = None,
    ) -> None:
        if config_path is None:
            config_path = resolve_config_path(
                env_var_name="AGENT_CONFIG_PATH",
                default_filename="config.yaml",
            )

        raw = self._load_raw_config(config_path)
        self._profiles = self._parse_profiles(raw, config_path)
        roster_handles = self._load_roster_handles(raw, config_path)

        self._agent_factory = AgentFactory(config_path=config_path, keys_dir=keys_dir)

        for name, profile in self._profiles.items():
            if profile.handle not in roster_handles:
                msg = (
                    f"Worker profile '{name}' references handle '{profile.handle}' "
                    f"which is not in roster.yaml"
                )
                raise KeyError(msg)

    def list_workers(self) -> list[str]:
        """Return the names of all available worker profiles."""
        return sorted(self._profiles.keys())

    def create_math_worker(self, worker_name: str) -> MathWorkerBundle:
        """Create a fully-wired math worker from a named profile.

        Args:
            worker_name: Name of the worker profile in config.yaml's
                workers section.

        Returns:
            A MathWorkerBundle containing the agent, LLM client, and loop.

        Raises:
            KeyError: If worker_name is not found in the profiles.
            ValueError: If the profile type is not 'math_worker'.
        """
        if worker_name not in self._profiles:
            available = ", ".join(sorted(self._profiles.keys()))
            msg = f"Worker profile '{worker_name}' not found. Available profiles: {available}"
            raise KeyError(msg)

        profile = self._profiles[worker_name]
        if profile.type != "math_worker":
            msg = f"Profile '{worker_name}' has type '{profile.type}', expected 'math_worker'"
            raise ValueError(msg)

        agent = self._agent_factory.create_agent(profile.handle)

        llm_config = LLMConfig(
            base_url=profile.llm.base_url,
            api_key=profile.llm.api_key.get_secret_value(),
            model_id=profile.llm.model_id,
            temperature=profile.llm.temperature,
            max_tokens=profile.llm.max_tokens,
        )

        worker_config = MathWorkerConfig(
            handle=profile.handle,
            scan_interval_seconds=profile.behavior.scan_interval_seconds,
            poll_interval_seconds=profile.behavior.poll_interval_seconds,
            max_poll_attempts=profile.behavior.max_poll_attempts,
            error_backoff_seconds=profile.behavior.error_backoff_seconds,
            min_reward=profile.behavior.min_reward,
            max_reward=profile.behavior.max_reward,
        )

        llm = LLMClient(llm_config)
        loop = MathWorkerLoop(agent=agent, llm=llm, config=worker_config)

        return MathWorkerBundle(agent=agent, llm=llm, loop=loop)

    @staticmethod
    def _load_raw_config(config_path: Path) -> dict[str, object]:
        raw = yaml.safe_load(config_path.read_text())
        if not isinstance(raw, dict):
            msg = f"Invalid config file: {config_path}"
            raise ValueError(msg)
        return raw

    @staticmethod
    def _parse_profiles(
        raw: dict[str, object],
        config_path: Path,
    ) -> dict[str, WorkerProfile]:
        workers_raw = raw.get("workers")
        if not isinstance(workers_raw, dict) or not workers_raw:
            msg = f"Config file must contain a non-empty 'workers' section: {config_path}"
            raise ValueError(msg)

        profiles: dict[str, WorkerProfile] = {}
        for name, profile_data in workers_raw.items():
            if not isinstance(name, str):
                msg = f"Worker profile name must be a string, got: {type(name).__name__}"
                raise ValueError(msg)
            if not isinstance(profile_data, dict):
                msg = (
                    f"Worker profile '{name}' must be a mapping, got: {type(profile_data).__name__}"
                )
                raise ValueError(msg)
            profiles[name] = WorkerProfile(**profile_data)
        return profiles

    @staticmethod
    def _load_roster_handles(raw: dict[str, object], config_path: Path) -> set[str]:
        data_section = raw.get("data")
        if not isinstance(data_section, dict):
            msg = f"Config file missing 'data' section: {config_path}"
            raise ValueError(msg)

        roster_path_raw = data_section.get("roster_path")
        if not isinstance(roster_path_raw, str) or roster_path_raw == "":
            msg = f"Config file missing valid data.roster_path: {config_path}"
            raise ValueError(msg)

        roster_path = Path(roster_path_raw)
        if not roster_path.is_absolute():
            roster_path = config_path.parent / roster_path

        roster_raw = yaml.safe_load(roster_path.read_text())
        if not isinstance(roster_raw, dict):
            msg = f"Invalid roster file: {roster_path}"
            raise ValueError(msg)

        roster_agents = roster_raw.get("agents")
        if not isinstance(roster_agents, dict):
            msg = f"Roster file missing 'agents' mapping: {roster_path}"
            raise ValueError(msg)

        return set(roster_agents.keys())
