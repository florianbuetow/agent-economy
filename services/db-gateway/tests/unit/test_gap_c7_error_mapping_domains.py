"""GAP-C7 coverage extension for the WP-11/B16 db_writer.py decomposition.

test_gap_c7_error_mapping.py's source-text scan only inspects db_writer.py.
After WP-11 moved the sqlite_errorcode-based IntegrityError classification
(is_foreign_key_violation/is_unique_violation) into db_writer_helpers.py, and
the actual write methods that call them into per-domain writer modules,
db_writer.py's own text no longer contains "sqlite_errorcode" even though the
classification is unchanged and still reachable. This file re-applies the
same two assertions to the modules that now actually hold this logic, so
GAP-C7's "no driver-message substring matching" guard stays meaningful.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SERVICES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "src" / "db_gateway_service" / "services"
)

_WRITE_MODULES = (
    "db_writer.py",
    "db_writer_helpers.py",
    "identity_writer.py",
    "bank_writer.py",
    "board_writer.py",
    "reputation_writer.py",
    "court_writer.py",
)


@pytest.mark.unit
class TestNoDriverMessageInspectionAcrossDomains:
    """Every write-path module must classify IntegrityError purely via
    sqlite_errorcode — never by inspecting the driver's raw message text."""

    def test_no_write_module_inspects_the_raw_exception_string(self) -> None:
        offenders = [
            filename
            for filename in _WRITE_MODULES
            if "str(exc)" in (_SERVICES_DIR / filename).read_text()
        ]
        assert offenders == [], (
            f"These modules inspect the raw IntegrityError message text: {offenders}; "
            "classify via exc.sqlite_errorcode instead (SEC-02: the message text "
            "contains real table/column names)"
        )

    def test_db_writer_helpers_uses_sqlite_errorcode_constants(self) -> None:
        source = (_SERVICES_DIR / "db_writer_helpers.py").read_text()
        assert "sqlite_errorcode" in source
        assert "SQLITE_CONSTRAINT_FOREIGNKEY" in source
