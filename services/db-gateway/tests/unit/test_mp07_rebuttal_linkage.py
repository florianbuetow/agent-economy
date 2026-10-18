"""Rebuttals require the immutable dispute facts frozen by Task Board."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.unit import test_mp07_rebuttal_projection as projection

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("field", ["reason", "rebuttal_deadline"])
def test_rebuttal_rejects_changed_frozen_claim_without_any_write(
    db_writer: DbWriter, field: str
) -> None:
    dispute = projection._seed_dispute(db_writer)
    if field == "reason":
        db_writer.connection.execute(
            "UPDATE court_claims SET reason='Different dispute' WHERE claim_id=?",
            (dispute.claim_id,),
        )
    else:
        db_writer.connection.execute(
            "UPDATE court_claims SET rebuttal_deadline='2099-01-03T00:00:04Z' WHERE claim_id=?",
            (dispute.claim_id,),
        )
    db_writer.connection.commit()

    projection._assert_rejected_without_effects(
        db_writer, projection._rebuttal(dispute), 409, "payload_mismatch"
    )
