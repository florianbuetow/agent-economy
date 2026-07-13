"""GAP-C5 coverage extension for the WP-11/B16 db_writer.py decomposition.

test_gap_c5_single_writer_invariant.py's AST/coroutine scan only inspects
db_writer.py and DbWriter's own members. After WP-11 split db_writer.py's
domain SQL out into per-domain writer modules (identity_writer.py,
bank_writer.py, board_writer.py, reputation_writer.py, court_writer.py) that
also run BEGIN IMMEDIATE through COMMIT/ROLLBACK on the same shared
connection, that scan no longer reaches the code doing the actual writes.
This file re-applies the identical checks to the five domain-writer modules
and their shared helpers, so the single-writer/no-await-in-transaction
invariant stays enforced everywhere it applies, not just in the facade.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from db_gateway_service.services.bank_writer import BankWriter
from db_gateway_service.services.board_writer import BoardWriter
from db_gateway_service.services.court_writer import CourtWriter
from db_gateway_service.services.identity_writer import IdentityWriter
from db_gateway_service.services.reputation_writer import ReputationWriter

_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
_SERVICES_DIR = _SERVICE_ROOT / "src" / "db_gateway_service" / "services"

_DOMAIN_MODULES = (
    "identity_writer.py",
    "bank_writer.py",
    "board_writer.py",
    "reputation_writer.py",
    "court_writer.py",
    "db_writer_helpers.py",
)

_DOMAIN_WRITER_CLASSES = (
    IdentityWriter,
    BankWriter,
    BoardWriter,
    ReputationWriter,
    CourtWriter,
)


@pytest.mark.architecture
class TestDomainWritersNoAwaitInvariant:
    """Cheap static regression guard, extended to the split-out domain writers."""

    def test_no_domain_writer_method_is_a_coroutine_function(self) -> None:
        """Every domain writer method must stay a plain `def` — see
        test_gap_c5_single_writer_invariant.py::test_no_db_writer_method_is_a_coroutine_function
        for why."""
        offenders: list[str] = []
        for cls in _DOMAIN_WRITER_CLASSES:
            offenders.extend(
                f"{cls.__name__}.{name}"
                for name, member in inspect.getmembers(cls)
                if inspect.iscoroutinefunction(member)
            )
        assert offenders == [], (
            f"Domain writer methods must not be async: {offenders}. "
            "An async write method could await between BEGIN IMMEDIATE and COMMIT, "
            "corrupting the single shared-connection transaction."
        )

    def test_domain_writer_modules_contain_no_await_expression(self) -> None:
        """AST-level guard: no `await` keyword anywhere in any domain writer module."""
        offenders: list[str] = []
        for filename in _DOMAIN_MODULES:
            path = _SERVICES_DIR / filename
            tree = ast.parse(path.read_text(), filename=str(path))
            if any(isinstance(node, ast.Await) for node in ast.walk(tree)):
                offenders.append(filename)
        assert offenders == [], (
            f"These domain writer modules contain an `await` expression: {offenders}. "
            "This can interleave another coroutine's write with an open BEGIN "
            "IMMEDIATE transaction on the shared connection."
        )
