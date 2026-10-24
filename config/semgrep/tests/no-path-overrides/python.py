import os
import subprocess


def rejected_python_overrides(monkeypatch: object, replacement: str) -> None:
    # ruleid: agent-economy.no-python-path-environment-overrides
    os.environ["PATH"] = replacement

    environment = dict(os.environ)
    # ruleid: agent-economy.no-python-path-environment-overrides
    environment["PATH"] = replacement

    # ruleid: agent-economy.no-python-path-environment-overrides
    os.environ.update({"PATH": replacement})
    # ruleid: agent-economy.no-python-path-environment-overrides
    os.environ.setdefault("PATH", replacement)
    # ruleid: agent-economy.no-python-path-environment-overrides
    os.environ.put("PATH", replacement)

    # ruleid: agent-economy.no-python-subprocess-path-overrides
    subprocess.run(["tool"], env={**os.environ, "PATH": replacement}, check=True)
    # ruleid: agent-economy.no-python-subprocess-path-overrides
    subprocess.run(["tool"], env={"PATH": replacement}, check=True)

    # ruleid: agent-economy.no-python-monkeypatch-path-overrides
    monkeypatch.setenv("PATH", replacement)


def accepted_python_path_usage() -> None:
    # ok: agent-economy.no-python-path-environment-overrides
    inherited_path = os.environ.get("PATH")
    # ok: agent-economy.no-python-path-environment-overrides
    environment = dict(os.environ)
    # ok: agent-economy.no-python-subprocess-path-overrides
    subprocess.run(["tool"], env=environment, check=True)
    # ok: agent-economy.no-python-path-environment-overrides
    path = "/api/tasks"
    assert inherited_path is None or path.startswith("/")
