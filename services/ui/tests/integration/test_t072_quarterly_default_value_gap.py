"""QTR-01 gap: GET /api/quarterly-report with no ``quarter`` param must
return the CURRENT quarter's report — not just *a* response containing a
quarter field.

tests/integration/test_quarterly.py's test_quarterly_default_current_quarter
only asserts the response contains "quarter" and "gdp" keys, never the
actual value. The ``client``/``app`` fixture in
tests/integration/conftest.py already monkeypatches
``ui_service.services.quarterly.utc_now`` to a fixed 2026-03-02, whose
calendar quarter is unambiguously 2026-Q1 — this test makes that value
assertion explicit, matching the spec's "quarter in the response matches
current_quarter_label()" requirement.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_default_quarter_is_current_quarter_2026_q1(client) -> None:
    """QTR-01: with utc_now frozen at 2026-03-02, the default quarter is 2026-Q1."""
    response = await client.get("/api/quarterly-report")
    assert response.status_code == 200
    assert response.json()["quarter"] == "2026-Q1"
