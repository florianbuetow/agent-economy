"""Map pending repository paths to the component gates a commit must pass.

Reads one path per line on stdin (as printed by ``git status --porcelain``
without the status columns; rename entries ``old -> new`` are accepted) and
prints one gate target per line, in a stable order:

* ``libs/<name>``, ``services/<name>``, ``agents``, ``tools`` — run that
  component's ``just ci-quiet``.
* ``.`` — root-level code (justfile, scripts/, tests/, config/) — run the
  root structure checks.

Documentation-only paths (``docs/``, ``openspec/``, ``*.md``) produce no
target. Used by ``scripts/ci-quiet-hook.sh``; tested in
``tests/runtime/test_ci_gate_targets.py``.
"""

from __future__ import annotations

import sys

_COMPONENT_PREFIXES = ("libs/", "services/")
_SINGLE_COMPONENTS = ("agents", "tools")
_ROOT_CODE_PREFIXES = ("scripts/", "tests/", "config/")
_ROOT_CODE_FILES = ("justfile", ".pre-commit-config.yaml", ".semgrepignore", ".gitignore")
_DOC_PREFIXES = ("docs/", "openspec/", "evidence/")
_ORDER = ("libs/", "services/", "agents", "tools", ".")


def _target_for(path: str) -> str | None:
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    path = path.strip().lstrip("./")
    if not path:
        return None
    if path.startswith(_DOC_PREFIXES) or path.endswith(".md"):
        return None
    for prefix in _COMPONENT_PREFIXES:
        if path.startswith(prefix):
            parts = path.split("/")
            if len(parts) >= 3:
                return f"{parts[0]}/{parts[1]}"
            return "."
    for component in _SINGLE_COMPONENTS:
        if path == component or path.startswith(component + "/"):
            return component
    if path.startswith(_ROOT_CODE_PREFIXES) or path in _ROOT_CODE_FILES:
        return "."
    return None


def _sort_key(target: str) -> tuple[int, str]:
    for index, prefix in enumerate(_ORDER):
        if target == prefix or target.startswith(prefix):
            return (index, target)
    return (len(_ORDER), target)


def main() -> int:
    targets: list[str] = []
    for line in sys.stdin.read().splitlines():
        target = _target_for(line)
        if target is not None and target not in targets:
            targets.append(target)
    for target in sorted(targets, key=_sort_key):
        print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
