# Phase 6 — Service Layer (Ledger)

## Working Directory

All paths relative to `services/central-bank/`.

---

## Task B6: Implement Ledger (business logic layer)

### Step 6.1: Write ledger.py

Create `services/central-bank/src/central_bank_service/services/ledger.py`:

```python
"""Ledger business logic — accounts, transactions, and escrow."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from service_commons.exceptions import ServiceError


class Ledger:
    """
    Manages accounts, transactions, and escrow.

    Uses SQLite for persistence. All balance mutations and their
    corresponding transaction log entries happen in a single
    database transaction for atomicity.
    """

    def __init__(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                tx_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(account_id),
                type TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount > 0),
                balance_after INTEGER NOT NULL,
                reference TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS escrow (
                escrow_id TEXT PRIMARY KEY,
                payer_account_id TEXT NOT NULL REFERENCES accounts(account_id),
                amount INTEGER NOT NULL CHECK (amount > 0),
                task_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'locked',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            """
        )
        self._db.commit()

    def _now(self) -> str:
        """Current UTC timestamp in ISO 8601 format."""
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _new_tx_id(self) -> str:
        """Generate a new transaction ID."""
        return f"tx-{uuid.uuid4()}"

    def _new_escrow_id(self) -> str:
        """Generate a new escrow ID."""
        return f"esc-{uuid.uuid4()}"

    def create_account(
        self,
        account_id: str,
        initial_balance: int,
    ) -> dict[str, object]:
        """
        Create a new account.

        Args:
            account_id: The agent_id from Identity service.
            initial_balance: Starting balance (>= 0).

        Returns:
            Account record dict.

        Raises:
            ServiceError: ACCOUNT_EXISTS if account already exists.
            ServiceError: INVALID_AMOUNT if initial_balance < 0.
        """
        if initial_balance < 0:
            raise ServiceError(
                "INVALID_AMOUNT",
                "Initial balance must be non-negative",
                400,
                {},
            )

        now = self._now()

        try:
            self._db.execute(
                "INSERT INTO accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
                (account_id, initial_balance, now),
            )
            if initial_balance > 0:
                tx_id = self._new_tx_id()
                self._db.execute(
                    "INSERT INTO transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tx_id, account_id, "credit", initial_balance, initial_balance, "initial_balance", now),
                )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise ServiceError(
                "ACCOUNT_EXISTS",
                "Account already exists for this agent",
                409,
                {},
            ) from exc

        return {
            "account_id": account_id,
            "balance": initial_balance,
            "created_at": now,
        }

    def get_account(self, account_id: str) -> dict[str, object] | None:
        """Look up an account by ID. Returns None if not found."""
        cursor = self._db.execute(
            "SELECT account_id, balance, created_at FROM accounts WHERE account_id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"account_id": row[0], "balance": row[1], "created_at": row[2]}

    def credit(
        self,
        account_id: str,
        amount: int,
        reference: str,
    ) -> dict[str, object]:
        """
        Add funds to an account.

        Args:
            account_id: Target account.
            amount: Positive integer.
            reference: Context string (e.g., "salary_round_3").

        Returns:
            {"tx_id": "...", "balance_after": N}

        Raises:
            ServiceError: ACCOUNT_NOT_FOUND, INVALID_AMOUNT.
        """
        if amount <= 0:
            raise ServiceError(
                "INVALID_AMOUNT",
                "Amount must be a positive integer",
                400,
                {},
            )

        now = self._now()
        tx_id = self._new_tx_id()

        account = self.get_account(account_id)
        if account is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

        current_balance = int(account["balance"])  # type: ignore[arg-type]
        new_balance = current_balance + amount

        self._db.execute(
            "UPDATE accounts SET balance = ? WHERE account_id = ?",
            (new_balance, account_id),
        )
        self._db.execute(
            "INSERT INTO transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tx_id, account_id, "credit", amount, new_balance, reference, now),
        )
        self._db.commit()

        return {"tx_id": tx_id, "balance_after": new_balance}

    def get_transactions(self, account_id: str) -> list[dict[str, object]]:
        """
        Get transaction history for an account.

        Raises:
            ServiceError: ACCOUNT_NOT_FOUND.
        """
        account = self.get_account(account_id)
        if account is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

        cursor = self._db.execute(
            "SELECT tx_id, type, amount, balance_after, reference, timestamp "
            "FROM transactions WHERE account_id = ? ORDER BY timestamp",
            (account_id,),
        )
        return [
            {
                "tx_id": row[0],
                "type": row[1],
                "amount": row[2],
                "balance_after": row[3],
                "reference": row[4],
                "timestamp": row[5],
            }
            for row in cursor.fetchall()
        ]

    def escrow_lock(
        self,
        payer_account_id: str,
        amount: int,
        task_id: str,
    ) -> dict[str, object]:
        """
        Lock funds in escrow.

        Debits the payer account and creates an escrow record.
        All in a single transaction.

        Raises:
            ServiceError: ACCOUNT_NOT_FOUND, INVALID_AMOUNT, INSUFFICIENT_FUNDS.
        """
        if amount <= 0:
            raise ServiceError(
                "INVALID_AMOUNT",
                "Amount must be a positive integer",
                400,
                {},
            )

        account = self.get_account(payer_account_id)
        if account is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

        current_balance = int(account["balance"])  # type: ignore[arg-type]
        if current_balance < amount:
            raise ServiceError(
                "INSUFFICIENT_FUNDS",
                "Insufficient funds for escrow lock",
                402,
                {},
            )

        now = self._now()
        tx_id = self._new_tx_id()
        escrow_id = self._new_escrow_id()
        new_balance = current_balance - amount

        self._db.execute(
            "UPDATE accounts SET balance = ? WHERE account_id = ?",
            (new_balance, payer_account_id),
        )
        self._db.execute(
            "INSERT INTO transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tx_id, payer_account_id, "escrow_lock", amount, new_balance, task_id, now),
        )
        self._db.execute(
            "INSERT INTO escrow (escrow_id, payer_account_id, amount, task_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (escrow_id, payer_account_id, amount, task_id, "locked", now),
        )
        self._db.commit()

        return {
            "escrow_id": escrow_id,
            "amount": amount,
            "task_id": task_id,
            "status": "locked",
        }

    def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
    ) -> dict[str, object]:
        """
        Release escrowed funds to a recipient.

        Raises:
            ServiceError: ESCROW_NOT_FOUND, ESCROW_ALREADY_RESOLVED, ACCOUNT_NOT_FOUND.
        """
        escrow = self._get_escrow(escrow_id)
        if escrow is None:
            raise ServiceError("ESCROW_NOT_FOUND", "Escrow not found", 404, {})
        if escrow["status"] != "locked":
            raise ServiceError(
                "ESCROW_ALREADY_RESOLVED",
                "Escrow has already been resolved",
                409,
                {},
            )

        recipient = self.get_account(recipient_account_id)
        if recipient is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "Recipient account not found", 404, {})

        amount = int(escrow["amount"])  # type: ignore[arg-type]
        now = self._now()
        tx_id = self._new_tx_id()
        new_balance = int(recipient["balance"]) + amount  # type: ignore[arg-type]

        self._db.execute(
            "UPDATE accounts SET balance = ? WHERE account_id = ?",
            (new_balance, recipient_account_id),
        )
        self._db.execute(
            "INSERT INTO transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tx_id, recipient_account_id, "escrow_release", amount, new_balance, escrow_id, now),
        )
        self._db.execute(
            "UPDATE escrow SET status = 'released', resolved_at = ? WHERE escrow_id = ?",
            (now, escrow_id),
        )
        self._db.commit()

        return {
            "escrow_id": escrow_id,
            "status": "released",
            "recipient": recipient_account_id,
            "amount": amount,
        }

    def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
    ) -> dict[str, object]:
        """
        Split escrowed funds between worker and poster.

        Worker gets floor(amount * worker_pct / 100), poster gets remainder.

        Raises:
            ServiceError: ESCROW_NOT_FOUND, ESCROW_ALREADY_RESOLVED, ACCOUNT_NOT_FOUND,
                INVALID_AMOUNT if worker_pct not in 0..100.
        """
        if not (0 <= worker_pct <= 100):
            raise ServiceError(
                "INVALID_AMOUNT",
                "worker_pct must be between 0 and 100",
                400,
                {},
            )

        escrow = self._get_escrow(escrow_id)
        if escrow is None:
            raise ServiceError("ESCROW_NOT_FOUND", "Escrow not found", 404, {})
        if escrow["status"] != "locked":
            raise ServiceError(
                "ESCROW_ALREADY_RESOLVED",
                "Escrow has already been resolved",
                409,
                {},
            )

        worker = self.get_account(worker_account_id)
        if worker is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "Worker account not found", 404, {})

        poster = self.get_account(poster_account_id)
        if poster is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "Poster account not found", 404, {})

        total_amount = int(escrow["amount"])  # type: ignore[arg-type]
        worker_amount = total_amount * worker_pct // 100
        poster_amount = total_amount - worker_amount

        now = self._now()

        # Credit worker
        if worker_amount > 0:
            worker_new_balance = int(worker["balance"]) + worker_amount  # type: ignore[arg-type]
            worker_tx_id = self._new_tx_id()
            self._db.execute(
                "UPDATE accounts SET balance = ? WHERE account_id = ?",
                (worker_new_balance, worker_account_id),
            )
            self._db.execute(
                "INSERT INTO transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (worker_tx_id, worker_account_id, "escrow_release", worker_amount, worker_new_balance, escrow_id, now),
            )

        # Credit poster
        if poster_amount > 0:
            poster_new_balance = int(poster["balance"]) + poster_amount  # type: ignore[arg-type]
            poster_tx_id = self._new_tx_id()
            self._db.execute(
                "UPDATE accounts SET balance = ? WHERE account_id = ?",
                (poster_new_balance, poster_account_id),
            )
            self._db.execute(
                "INSERT INTO transactions (tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (poster_tx_id, poster_account_id, "escrow_release", poster_amount, poster_new_balance, escrow_id, now),
            )

        self._db.execute(
            "UPDATE escrow SET status = 'split', resolved_at = ? WHERE escrow_id = ?",
            (now, escrow_id),
        )
        self._db.commit()

        return {
            "escrow_id": escrow_id,
            "status": "split",
            "worker_amount": worker_amount,
            "poster_amount": poster_amount,
        }

    def count_accounts(self) -> int:
        """Count total accounts."""
        cursor = self._db.execute("SELECT COUNT(*) FROM accounts")
        result = cursor.fetchone()
        if result is None:
            return 0
        return int(result[0])

    def total_escrowed(self) -> int:
        """Sum of all locked (unresolved) escrow amounts."""
        cursor = self._db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM escrow WHERE status = 'locked'"
        )
        result = cursor.fetchone()
        if result is None:
            return 0
        return int(result[0])

    def _get_escrow(self, escrow_id: str) -> dict[str, object] | None:
        """Look up an escrow record."""
        cursor = self._db.execute(
            "SELECT escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at "
            "FROM escrow WHERE escrow_id = ?",
            (escrow_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "escrow_id": row[0],
            "payer_account_id": row[1],
            "amount": row[2],
            "task_id": row[3],
            "status": row[4],
            "created_at": row[5],
            "resolved_at": row[6],
        }

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()
```

### Step 6.2: Update services/__init__.py

Replace the empty `services/central-bank/src/central_bank_service/services/__init__.py` with:

```python
"""Service layer components."""

from central_bank_service.services.identity_client import IdentityClient
from central_bank_service.services.ledger import Ledger

__all__ = ["IdentityClient", "Ledger"]
```

### Step 6.3: Commit

```bash
git add services/central-bank/src/central_bank_service/services/
git commit -m "feat(central-bank): add Ledger and IdentityClient service layer"
```

---

## Verification

```bash
cd services/central-bank && uv run ruff check src/ && uv run ruff format --check src/
```
