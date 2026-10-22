"""Assertions for the live MP-05 browser posting conservation proof."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

RECORD_PREFIX = "ate.task-posting:v1:"
COMPLETION_PREFIX = "ate.task-posting-completion:v1:"


def read_single_posting(page: Any) -> tuple[str, dict[str, Any]]:
    """Return the one immutable posting record saved by the shipped browser code."""
    stored = page.evaluate(
        """prefix => Object.keys(localStorage)
          .filter(key => key.startsWith(prefix))
          .map(key => [key, JSON.parse(localStorage.getItem(key))])""",
        RECORD_PREFIX,
    )
    assert isinstance(stored, list)
    assert len(stored) == 1
    key, record = stored[0]
    assert isinstance(key, str)
    assert isinstance(record, dict)
    operation_id = record["intent"]["operation_id"]
    assert key == f"{RECORD_PREFIX}{operation_id}"
    return operation_id, record


def assert_completion_marker(page: Any, record: dict[str, Any]) -> None:
    """Require one durable completion marker bound to the original intent."""
    operation_id = record["intent"]["operation_id"]
    raw = page.evaluate(
        "key => localStorage.getItem(key)",
        f"{COMPLETION_PREFIX}{operation_id}",
    )
    assert isinstance(raw, str)
    marker = json.loads(raw)
    assert marker["operation_id"] == operation_id
    assert marker["task_id"] == record["intent"]["task_id"]
    assert marker["request_sha256"] == record["request_sha256"]
    assert marker["publication"]["publication_state"] == "published"


def operator_balance(database_path: Path, operator_id: str) -> int:
    """Read the operator balance without mutating the isolated Gateway database."""
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT balance FROM bank_accounts WHERE account_id=?",
            (operator_id,),
        ).fetchone()
    assert row is not None
    assert type(row[0]) is int
    return row[0]


def assert_conserved_publication(
    database_path: Path,
    record: dict[str, Any],
    initial_balance: int,
) -> None:
    """Bind the browser intent to exactly one task, lock, debit, and event pair."""
    intent = record["intent"]
    operation_id = intent["operation_id"]
    task_id = intent["task_id"]
    operator_id = intent["actor_id"]
    reward = intent["reward"]

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        tasks = connection.execute(
            "SELECT task_id,poster_id,reward,escrow_id,event_id FROM board_tasks "
            "WHERE task_id=?",
            (task_id,),
        ).fetchall()
        publications = connection.execute(
            "SELECT operation_id,task_id,request_sha256,lock_operation_id,state,escrow_id,"
            "lock_receipt_event_id,creation_event_id FROM board_task_publications "
            "WHERE operation_id=?",
            (operation_id,),
        ).fetchall()
        escrows = connection.execute(
            "SELECT escrow_id,payer_account_id,amount,task_id,status,event_id "
            "FROM bank_escrow WHERE task_id=?",
            (task_id,),
        ).fetchall()
        locks = connection.execute(
            "SELECT tx_id,account_id,type,amount,balance_after,reference,event_id "
            "FROM bank_transactions WHERE account_id=? AND type='escrow_lock' AND reference=?",
            (operator_id, task_id),
        ).fetchall()
        events = connection.execute(
            "SELECT event_id,event_source,event_type,task_id,agent_id FROM events "
            "WHERE task_id=? ORDER BY event_id",
            (task_id,),
        ).fetchall()
        balance_row = connection.execute(
            "SELECT balance FROM bank_accounts WHERE account_id=?",
            (operator_id,),
        ).fetchone()

    assert len(tasks) == len(publications) == len(escrows) == len(locks) == 1
    task = tasks[0]
    publication = publications[0]
    escrow = escrows[0]
    lock = locks[0]
    assert balance_row is not None

    assert dict(task) == {
        "task_id": task_id,
        "poster_id": operator_id,
        "reward": reward,
        "escrow_id": escrow["escrow_id"],
        "event_id": publication["creation_event_id"],
    }
    assert dict(publication) == {
        "operation_id": operation_id,
        "task_id": task_id,
        "request_sha256": record["request_sha256"],
        "lock_operation_id": intent["lock_operation_id"],
        "state": "published",
        "escrow_id": escrow["escrow_id"],
        "lock_receipt_event_id": escrow["event_id"],
        "creation_event_id": task["event_id"],
    }
    assert dict(escrow) == {
        "escrow_id": task["escrow_id"],
        "payer_account_id": operator_id,
        "amount": reward,
        "task_id": task_id,
        "status": "locked",
        "event_id": lock["event_id"],
    }
    assert dict(lock) == {
        "tx_id": lock["tx_id"],
        "account_id": operator_id,
        "type": "escrow_lock",
        "amount": reward,
        "balance_after": initial_balance - reward,
        "reference": task_id,
        "event_id": escrow["event_id"],
    }
    assert [
        (event["event_id"], event["event_source"], event["event_type"])
        for event in events
    ] == [
        (escrow["event_id"], "central-bank", "escrow.locked"),
        (task["event_id"], "board", "task.created"),
    ]
    assert all(event["task_id"] == task_id for event in events)
    assert all(event["agent_id"] == operator_id for event in events)
    assert balance_row["balance"] == initial_balance - reward
    assert balance_row["balance"] + escrow["amount"] == initial_balance
