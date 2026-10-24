"""Demo entry points open the configured UI before replaying scenarios."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_UI_URL = "http://localhost:8008"
_RUN_ID = "run-00000000-0000-4000-8000-000000000592"


def _load_opener() -> ModuleType:
    path = _ROOT / "scripts" / "open_ui.py"
    spec = importlib.util.spec_from_file_location("open_ui", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ui_opener_prints_url_and_uses_new_browser_tab(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opener = _load_opener()
    calls: list[tuple[str, int]] = []

    def record_open(url: str, new: int) -> bool:
        calls.append((url, new))
        return True

    monkeypatch.setattr(opener.webbrowser, "open", record_open)

    assert opener.main(["--url", _UI_URL]) == 0
    assert calls == [(_UI_URL, 2)]
    assert _UI_URL in capsys.readouterr().out


def test_ui_opener_reports_failure_and_manual_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opener = _load_opener()
    monkeypatch.setattr(opener.webbrowser, "open", lambda url, new: False)

    assert opener.main(["--url", _UI_URL]) == 1
    captured = capsys.readouterr()
    assert _UI_URL in captured.out
    assert "could not open" in captured.err.lower()
    assert "open it manually" in captured.err.lower()


@pytest.mark.parametrize(
    ("recipe", "arguments", "scenario"),
    [
        ("demo", (), "quick"),
        ("demo-scale", (), "scale"),
        ("demo-resume", (_RUN_ID,), "quick"),
        ("demo-scale-resume", (_RUN_ID,), "scale"),
    ],
)
def test_demo_opens_ui_before_replay(
    recipe: str,
    arguments: tuple[str, ...],
    scenario: str,
) -> None:
    result = subprocess.run(
        ("just", "--dry-run", recipe, *arguments),
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    command = result.stdout + result.stderr
    opener = f"scripts/open_ui.py --url {_UI_URL}"
    replay = f"scenarios/{scenario}.yaml"
    assert opener in command
    assert command.index(opener) < command.index(replay)
    for forbidden in ("start-all", "stop-all", "rm ", "lsof", "read -r"):
        assert forbidden not in command
