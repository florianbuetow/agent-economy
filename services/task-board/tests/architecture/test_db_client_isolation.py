"""Architecture tests: only *DbClient may import httpx."""

import ast
from pathlib import Path

import pytest

_SERVICE_PACKAGE = "task_board_service"
_DB_CLIENT_MODULE = "task_db_client"
_SERVICE_SRC = Path(__file__).resolve().parents[2] / "src" / _SERVICE_PACKAGE


def _iter_production_files() -> list[Path]:
    return [p for p in _SERVICE_SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _extract_imports(filepath: Path) -> set[str]:
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


@pytest.mark.architecture
class TestDbClientIsolation:
    def test_only_http_clients_import_httpx(self) -> None:
        allowed = {_DB_CLIENT_MODULE, "identity_client", "central_bank_client"}
        violations: list[str] = []
        for py_file in _iter_production_files():
            if py_file.stem in allowed:
                continue
            if "httpx" in _extract_imports(py_file):
                violations.append(str(py_file.relative_to(_SERVICE_SRC)))
        assert violations == [], f"Unexpected httpx import in: {violations}"

    def test_lifespan_does_not_import_legacy_stores(self) -> None:
        lifespan = _SERVICE_SRC / "core" / "lifespan.py"
        imports = _extract_imports(lifespan)
        legacy_modules = {
            module
            for module in imports
            if "in_memory" in module
            or "sqlite_feedback" in module
            or "agent_store" in module.split(".")[-1]
        }
        assert legacy_modules == set(), (
            f"lifespan.py imports legacy store modules: {legacy_modules}"
        )

    def test_lifespan_requires_db_gateway(self) -> None:
        lifespan = _SERVICE_SRC / "core" / "lifespan.py"
        content = lifespan.read_text()
        assert "db_gateway configuration is required" in content, (
            "lifespan must raise RuntimeError when db_gateway is None"
        )
