"""Darwin process metadata uses libproc without filesystem-wide lsof probes."""

from __future__ import annotations

import ctypes
import os

import pytest

import scripts.stop_agent as stop_agent_module
from scripts.stop_agent import (
    ProcessAbsentError,
    _DarwinVnodePathInfo,
    _MAXPATHLEN,
    _darwin_cwd,
)

pytestmark = pytest.mark.unit


def test_vnode_path_layout_matches_installed_macos_sdk() -> None:
    assert ctypes.sizeof(_DarwinVnodePathInfo) == 2352
    assert _MAXPATHLEN == 1024


def test_missing_process_during_cwd_read_is_explicit_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        stop_agent_module,
        "_darwin_proc_pidinfo",
        lambda *_args: (_ for _ in ()).throw(ProcessAbsentError(71040)),
    )

    with pytest.raises(ProcessAbsentError):
        _darwin_cwd(71040)


def test_unreadable_cwd_on_same_live_birth_is_not_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel_identity = (os.getuid(), 1, "unchanged-birth")
    monkeypatch.setattr(
        stop_agent_module, "_darwin_bsd_identity", lambda _pid: kernel_identity
    )
    monkeypatch.setattr(
        stop_agent_module,
        "_darwin_cwd",
        lambda _pid: (_ for _ in ()).throw(RuntimeError("cwd unavailable")),
    )

    with pytest.raises(RuntimeError, match="complete metadata for live PID"):
        stop_agent_module._darwin_process_identity(71040)
