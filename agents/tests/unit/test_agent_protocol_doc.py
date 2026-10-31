"""The documented agent protocol cannot drift from the real helpers."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
from service_auth.financial_identity import operation_id_for

DOC_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "specifications"
    / "service-api"
    / "agent-protocol.md"
)
PREFIX = "https://agent-economy.invalid/financial-operations/v1/"
DOCUMENTED_ACTIONS = (
    "create_account",
    "get_balance",
    "get_transactions",
    "create_task",
    "escrow_lock",
    "get_task_publication",
    "submit_bid",
    "list_bids",
    "accept_bid",
    "upload_asset",
    "submit_deliverable",
    "approve_task",
    "dispute_task",
    "cancel_task",
    "submit_rebuttal",
    "submit_feedback",
)


def _vector_field(doc: str, name: str) -> str:
    match = re.search(rf"^{name}: (.+)$", doc, flags=re.MULTILINE)
    assert match is not None, f"test vector field {name} missing from {DOC_PATH}"
    return match.group(1)


@pytest.mark.unit
def test_documented_lock_operation_id_matches_sdk_helper() -> None:
    doc = DOC_PATH.read_text()
    task_id = _vector_field(doc, "task_id")
    poster_id = _vector_field(doc, "poster_id")

    assert _vector_field(doc, "lock_operation_id") == operation_id_for(
        "task-lock", (task_id, poster_id)
    )


@pytest.mark.unit
def test_documented_recipe_reproduces_the_vector_without_the_helper() -> None:
    doc = DOC_PATH.read_text()
    task_id = _vector_field(doc, "task_id")
    poster_id = _vector_field(doc, "poster_id")
    canonical = json.dumps(
        {"identity": [task_id, poster_id], "purpose": "task-lock", "version": 1},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    assert _vector_field(doc, "canonical") == canonical
    derived = f"op-{uuid.uuid5(uuid.NAMESPACE_URL, PREFIX + canonical)}"
    assert _vector_field(doc, "lock_operation_id") == derived


@pytest.mark.unit
@pytest.mark.parametrize("action", DOCUMENTED_ACTIONS)
def test_every_agent_action_is_documented(action: str) -> None:
    assert f'"{action}"' in DOC_PATH.read_text()
