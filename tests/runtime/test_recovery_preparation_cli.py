"""The isolated runtime exposes explicit, failure-visible recovery preparation."""

from __future__ import annotations

import argparse
import json
import signal
from typing import TYPE_CHECKING

import pytest
from scripts import isolated_test_runtime as runtime_module
from scripts.recovery_preparation import PreparedRecovery, RecoveryPreparationError

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_prepare_cli_requires_and_retains_explicit_registry_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "isolated_test_runtime.py",
            "--prepare-recovery",
            "--registry-timeout-seconds",
            "2.5",
            "--registry-max-attempts",
            "3",
            "--",
            "/usr/bin/true",
        ],
    )
    args = runtime_module._parse_args()
    assert args.prepare_recovery is True
    assert args.registry_timeout_seconds == 2.5
    assert args.registry_max_attempts == 3
    assert args.command == ["--", "/usr/bin/true"]


@pytest.mark.parametrize(
    "options",
    [
        ["--prepare-recovery"],
        ["--prepare-recovery", "--registry-timeout-seconds", "1"],
        ["--prepare-recovery", "--registry-timeout-seconds", "0", "--registry-max-attempts", "2"],
        ["--prepare-recovery", "--registry-timeout-seconds", "nan", "--registry-max-attempts", "2"],
        ["--prepare-recovery", "--registry-timeout-seconds", "1", "--registry-max-attempts", "0"],
        ["--registry-timeout-seconds", "1", "--registry-max-attempts", "2"],
        [
            "--prepare-recovery",
            "--registry-timeout-seconds",
            "1",
            "--registry-max-attempts",
            "2",
            "--services",
            "db-gateway",
        ],
    ],
)
def test_invalid_prepare_options_fail_before_runtime_creation(
    monkeypatch: pytest.MonkeyPatch,
    options: list[str],
) -> None:
    monkeypatch.setattr("sys.argv", ["isolated_test_runtime.py", *options, "--", "/usr/bin/true"])
    monkeypatch.setattr(
        runtime_module.IsolatedRuntime,
        "create",
        lambda **_kwargs: pytest.fail("created runtime before validating options"),
    )
    with pytest.raises(SystemExit) as exc:
        runtime_module.main()
    assert exc.value.code == 2


class _Runtime:
    def __init__(self, root: Path, phase_exit: int, prepare_error: bool) -> None:
        self.run_dir = root
        self.phase_exit = phase_exit
        self.prepare_error = prepare_error
        self.shutdown_signal = None
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def run_command(self, command: list[str]) -> int:
        assert command == ["/usr/bin/true"]
        self.calls.append("command")
        return self.phase_exit

    def stop(self) -> None:
        self.calls.append("stop")

    def prepare_recovery_and_stop(
        self, *, registry_timeout_seconds: float, registry_max_attempts: int
    ) -> PreparedRecovery:
        assert registry_timeout_seconds == 2.5
        assert registry_max_attempts == 3
        self.calls.append("prepare")
        if self.prepare_error:
            raise RecoveryPreparationError("source_changed", "DO-NOT-ECHO-PRIVATE-DETAIL")
        return PreparedRecovery(
            inventory_path=self.run_dir / "recovery-inventory.json",
            inventory_sha256="a" * 64,
            runtime_manifest_path=self.run_dir / "runtime.json",
        )


def _route(monkeypatch: pytest.MonkeyPatch, runtime: _Runtime, prepare: bool) -> int:
    args = argparse.Namespace(
        artifacts_root=None,
        provision=False,
        services=",".join(runtime_module.SERVICE_ORDER),
        command=["--", "/usr/bin/true"],
        prepare_recovery=prepare,
        registry_timeout_seconds=2.5 if prepare else None,
        registry_max_attempts=3 if prepare else None,
    )
    monkeypatch.setattr(runtime_module, "_parse_args", lambda: args)
    monkeypatch.setattr(runtime_module.IsolatedRuntime, "create", lambda **_kwargs: runtime)
    monkeypatch.setattr(signal, "signal", lambda *_args: None)
    return runtime_module.main()


@pytest.mark.parametrize("phase_exit", [0, 7])
def test_cli_prepares_after_a_drained_successful_or_failed_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase_exit: int,
) -> None:
    runtime = _Runtime(tmp_path, phase_exit, False)
    assert _route(monkeypatch, runtime, True) == phase_exit
    assert runtime.calls == ["start", "command", "prepare", "stop"]
    output = capsys.readouterr().out.splitlines()
    metadata = json.loads(output[-1])
    assert metadata["recovery_inventory"] == str(tmp_path / "recovery-inventory.json")
    assert metadata["inventory_sha256"] == "a" * 64


def test_cli_preparation_failure_is_nonzero_redacted_and_still_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = _Runtime(tmp_path, 0, True)
    assert _route(monkeypatch, runtime, True) == 1
    assert runtime.calls == ["start", "command", "prepare", "stop"]
    output = capsys.readouterr()
    assert "source_changed" in output.err
    assert "DO-NOT-ECHO" not in output.out + output.err


def test_normal_cli_shutdown_does_not_prepare_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _Runtime(tmp_path, 0, False)
    assert _route(monkeypatch, runtime, False) == 0
    assert runtime.calls == ["start", "command", "stop"]


@pytest.mark.parametrize("arguments", [[], ["--"]])
def test_empty_normalized_command_is_usage_error_before_runtime_creation(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    monkeypatch.setattr("sys.argv", ["isolated_test_runtime.py", *arguments])
    monkeypatch.setattr(
        runtime_module.IsolatedRuntime,
        "create",
        lambda **_kwargs: pytest.fail("created runtime for an empty command"),
    )
    assert runtime_module.main() == 2
