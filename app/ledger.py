"""Atomic double-entry ledger and idempotent transfer operations."""

from pathlib import Path
import sqlite3
import uuid

from app.db import get_connection
from app.fraud import (
    FraudLimitExceededError,
    VELOCITY_WINDOW_SECONDS,
    check_single_transfer_limit,
    check_velocity,
)


class AccountNotFoundError(Exception):
    """Raised when a sender or recipient account does not exist."""


class InvalidTransferError(Exception):
    """Raised when transfer parameters fail domain validation."""


class InsufficientFundsError(Exception):
    """Raised when the sender account lacks sufficient balance."""


class DuplicateIdempotencyKeyError(Exception):
    """Raised when an Idempotency-Key was already processed."""

    def __init__(self, existing_transfer: dict) -> None:
        self.existing_transfer = existing_transfer
        super().__init__(
            f"Idempotency-Key '{existing_transfer['idempotency_key']}' was already processed."
        )


def format_cents(cents: int) -> str:
    """Format an integer cent amount as USD without floating-point arithmetic."""
    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    return f"{sign}${abs_cents // 100:,}.{abs_cents % 100:02d}"


def _serialize_account(row: sqlite3.Row) -> dict:
    balance_cents = int(row["balance_cents"])
    return {
        "id": row["id"],
        "name": row["name"],
        "balance_cents": balance_cents,
        "balance_formatted": format_cents(balance_cents),
        "flagged": bool(row["flagged"]),
        "flag_reason": row["flag_reason"],
        "created_at": row["created_at"],
    }


def _serialize_transfer(row: sqlite3.Row) -> dict:
    amount_cents = int(row["amount_cents"])
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "from_account_id": row["from_account_id"],
        "to_account_id": row["to_account_id"],
        "amount_cents": amount_cents,
        "amount_formatted": format_cents(amount_cents),
        "status": row["status"],
        "reason": row["reason"],
        "velocity_flagged": bool(row["velocity_flagged"]),
        "created_at": row["created_at"],
    }


def _serialize_ledger_entry(row: sqlite3.Row) -> dict:
    amount_cents = int(row["amount_cents"])
    balance_after_cents = int(row["balance_after_cents"])
    return {
        "id": int(row["id"]),
        "transfer_id": row["transfer_id"],
        "account_id": row["account_id"],
        "entry_type": row["entry_type"],
        "amount_cents": amount_cents,
        "amount_formatted": format_cents(amount_cents),
        "balance_after_cents": balance_after_cents,
        "balance_after_formatted": format_cents(balance_after_cents),
        "created_at": row["created_at"],
    }


def list_accounts(db_path: Path | None = None) -> list[dict]:
    """Return all accounts ordered by name."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, name, balance_cents, flagged, flag_reason, created_at
            FROM accounts
            ORDER BY name ASC
            """
        ).fetchall()
        return [_serialize_account(row) for row in rows]
    finally:
        conn.close()


def list_transfers(db_path: Path | None = None, limit: int = 25) -> list[dict]:
    """Return recent transfers ordered newest first."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, idempotency_key, from_account_id, to_account_id,
                   amount_cents, status, reason, velocity_flagged, created_at
            FROM transfers
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_serialize_transfer(row) for row in rows]
    finally:
        conn.close()


def list_ledger_entries(db_path: Path | None = None, limit: int = 50) -> list[dict]:
    """Return recent double-entry ledger rows ordered newest first."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, transfer_id, account_id, entry_type,
                   amount_cents, balance_after_cents, created_at
            FROM ledger_entries
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_serialize_ledger_entry(row) for row in rows]
    finally:
        conn.close()


def execute_transfer(
    from_account_id: str,
    to_account_id: str,
    amount_cents: int,
    idempotency_key: str,
    db_path: Path | None = None,
) -> dict:
    """Execute an atomic P2P transfer with fraud, balance, and idempotency checks."""
    if type(amount_cents) is not int or amount_cents <= 0:
        raise InvalidTransferError("amount_cents must be a positive integer.")

    sender_id = from_account_id.strip()
    recipient_id = to_account_id.strip()
    clean_key = idempotency_key.strip()

    if not sender_id or not recipient_id:
        raise InvalidTransferError("Both sender and recipient accounts are required.")
    if sender_id == recipient_id:
        raise InvalidTransferError("Sender and recipient accounts must be different.")
    if not clean_key:
        raise InvalidTransferError("Idempotency-Key header must not be empty.")

    conn = get_connection(db_path)
    in_tx = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        in_tx = True

        existing = conn.execute(
            """
            SELECT id, idempotency_key, from_account_id, to_account_id,
                   amount_cents, status, reason, velocity_flagged, created_at
            FROM transfers
            WHERE idempotency_key = ?
            """,
            (clean_key,),
        ).fetchone()
        if existing is not None:
            conn.execute("ROLLBACK")
            in_tx = False
            raise DuplicateIdempotencyKeyError(_serialize_transfer(existing))

        sender = conn.execute(
            "SELECT id, name, balance_cents, flagged, flag_reason, created_at FROM accounts WHERE id = ?",
            (sender_id,),
        ).fetchone()
        if sender is None:
            conn.execute("ROLLBACK")
            in_tx = False
            raise AccountNotFoundError(f"Sender account '{sender_id}' not found.")

        recipient = conn.execute(
            "SELECT id, name, balance_cents, flagged, flag_reason, created_at FROM accounts WHERE id = ?",
            (recipient_id,),
        ).fetchone()
        if recipient is None:
            conn.execute("ROLLBACK")
            in_tx = False
            raise AccountNotFoundError(f"Recipient account '{recipient_id}' not found.")

        transfer_id = f"tx_{uuid.uuid4().hex[:12]}"

        try:
            check_single_transfer_limit(amount_cents)
        except FraudLimitExceededError as err:
            conn.execute(
                """
                INSERT INTO transfers (
                    id, idempotency_key, from_account_id, to_account_id,
                    amount_cents, status, reason, velocity_flagged
                )
                VALUES (?, ?, ?, ?, ?, 'BLOCKED_FRAUD', ?, 0)
                """,
                (transfer_id, clean_key, sender_id, recipient_id, amount_cents, err.message),
            )
            conn.execute("COMMIT")
            in_tx = False
            raise

        sender_balance = int(sender["balance_cents"])
        recipient_balance = int(recipient["balance_cents"])
        if sender_balance < amount_cents:
            conn.execute("ROLLBACK")
            in_tx = False
            raise InsufficientFundsError(
                f"Insufficient funds in '{sender['name']}': available {format_cents(sender_balance)}, "
                f"requested {format_cents(amount_cents)}."
            )

        is_flagged, recent_count = check_velocity(conn, sender_id)
        flag_reason = (
            f"Velocity alert: {recent_count} transfers within {VELOCITY_WINDOW_SECONDS}s"
            if is_flagged
            else None
        )

        new_sender_balance = sender_balance - amount_cents
        new_recipient_balance = recipient_balance + amount_cents

        if is_flagged:
            conn.execute(
                """
                UPDATE accounts
                SET balance_cents = ?, flagged = 1, flag_reason = ?
                WHERE id = ?
                """,
                (new_sender_balance, flag_reason, sender_id),
            )
        else:
            conn.execute(
                "UPDATE accounts SET balance_cents = ? WHERE id = ?",
                (new_sender_balance, sender_id),
            )

        conn.execute(
            "UPDATE accounts SET balance_cents = ? WHERE id = ?",
            (new_recipient_balance, recipient_id),
        )

        conn.execute(
            """
            INSERT INTO transfers (
                id, idempotency_key, from_account_id, to_account_id,
                amount_cents, status, reason, velocity_flagged
            )
            VALUES (?, ?, ?, ?, ?, 'COMPLETED', ?, ?)
            """,
            (
                transfer_id,
                clean_key,
                sender_id,
                recipient_id,
                amount_cents,
                flag_reason,
                1 if is_flagged else 0,
            ),
        )

        conn.execute(
            """
            INSERT INTO ledger_entries (
                transfer_id, account_id, entry_type, amount_cents, balance_after_cents
            )
            VALUES (?, ?, 'DEBIT', ?, ?)
            """,
            (transfer_id, sender_id, amount_cents, new_sender_balance),
        )
        conn.execute(
            """
            INSERT INTO ledger_entries (
                transfer_id, account_id, entry_type, amount_cents, balance_after_cents
            )
            VALUES (?, ?, 'CREDIT', ?, ?)
            """,
            (transfer_id, recipient_id, amount_cents, new_recipient_balance),
        )

        transfer_row = conn.execute(
            """
            SELECT id, idempotency_key, from_account_id, to_account_id,
                   amount_cents, status, reason, velocity_flagged, created_at
            FROM transfers
            WHERE id = ?
            """,
            (transfer_id,),
        ).fetchone()
        account_rows = conn.execute(
            """
            SELECT id, name, balance_cents, flagged, flag_reason, created_at
            FROM accounts
            ORDER BY name ASC
            """
        ).fetchall()

        conn.execute("COMMIT")
        in_tx = False

        return {
            "transfer": _serialize_transfer(transfer_row),
            "accounts": [_serialize_account(row) for row in account_rows],
            "velocity_flagged": is_flagged,
            "velocity_alert": flag_reason,
        }
    except Exception:
        if in_tx:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
