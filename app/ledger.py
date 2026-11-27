"""Atomic double-entry multi-currency ledger operations and query helpers."""

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from app.db import get_connection
from app.fraud import (
    VELOCITY_WINDOW_SECONDS,
    FraudLimitExceededError,
    check_single_transfer_limit,
    check_velocity,
)
from app.fx import (
    convert_minor_units,
    format_minor_units,
    minor_units_to_decimal_str,
    parse_decimal_to_minor_units,
)

_ACCOUNT_COLS = "id, name, currency, balance_cents, flagged, flag_reason, created_at"
_TRANSFER_COLS = (
    "id, idempotency_key, from_account_id, to_account_id, "
    "source_currency, target_currency, amount_cents, target_amount_cents, "
    "usd_equivalent_cents, fx_rate, status, reason, velocity_flagged, created_at"
)
_LEDGER_COLS = (
    "id, transfer_id, account_id, currency, entry_type, "
    "amount_cents, balance_after_cents, created_at"
)


class LedgerError(Exception):
    """Base exception for domain errors in the ledger."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AccountNotFoundError(LedgerError):
    """Raised when a referenced sender or recipient account does not exist."""


class InvalidTransferError(LedgerError):
    """Raised when transfer inputs fail validation or FX conversion."""


class InsufficientFundsError(LedgerError):
    """Raised when the sender lacks sufficient balance for the transfer."""


class DuplicateIdempotencyKeyError(LedgerError):
    """Raised when an idempotency key has already been recorded."""

    def __init__(
        self,
        existing_transfer: dict[str, Any],
        message: str = "A transfer with this Idempotency-Key has already been processed.",
    ) -> None:
        super().__init__(message)
        self.existing_transfer = existing_transfer


def serialize_account(row: sqlite3.Row) -> dict[str, Any]:
    """Serialize an accounts table row into an API-ready dictionary."""
    currency = row["currency"]
    balance_cents = int(row["balance_cents"])
    return {
        "id": row["id"],
        "name": row["name"],
        "currency": currency,
        "balance_cents": balance_cents,
        "balance": minor_units_to_decimal_str(balance_cents),
        "balance_formatted": format_minor_units(balance_cents, currency),
        "flagged": bool(row["flagged"]),
        "flag_reason": row["flag_reason"],
        "created_at": row["created_at"],
    }


def serialize_transfer(row: sqlite3.Row) -> dict[str, Any]:
    """Serialize a transfers table row into an API-ready dictionary."""
    source_currency = row["source_currency"]
    target_currency = row["target_currency"]
    amount_cents = int(row["amount_cents"])
    target_amount_cents = int(row["target_amount_cents"])
    usd_equivalent_cents = int(row["usd_equivalent_cents"])
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "from_account_id": row["from_account_id"],
        "to_account_id": row["to_account_id"],
        "source_currency": source_currency,
        "target_currency": target_currency,
        "amount_cents": amount_cents,
        "amount": minor_units_to_decimal_str(amount_cents),
        "amount_formatted": format_minor_units(amount_cents, source_currency),
        "target_amount_cents": target_amount_cents,
        "target_amount": minor_units_to_decimal_str(target_amount_cents),
        "target_amount_formatted": format_minor_units(target_amount_cents, target_currency),
        "usd_equivalent_cents": usd_equivalent_cents,
        "usd_equivalent": minor_units_to_decimal_str(usd_equivalent_cents),
        "fx_rate": row["fx_rate"],
        "status": row["status"],
        "reason": row["reason"],
        "velocity_flagged": bool(row["velocity_flagged"]),
        "created_at": row["created_at"],
    }


def serialize_ledger_entry(row: sqlite3.Row) -> dict[str, Any]:
    """Serialize a ledger_entries table row into an API-ready dictionary."""
    currency = row["currency"]
    amount_cents = int(row["amount_cents"])
    balance_after_cents = int(row["balance_after_cents"])
    return {
        "id": int(row["id"]),
        "transfer_id": row["transfer_id"],
        "account_id": row["account_id"],
        "currency": currency,
        "entry_type": row["entry_type"],
        "amount_cents": amount_cents,
        "amount": minor_units_to_decimal_str(amount_cents),
        "amount_formatted": format_minor_units(amount_cents, currency),
        "balance_after_cents": balance_after_cents,
        "balance_after": minor_units_to_decimal_str(balance_after_cents),
        "balance_after_formatted": format_minor_units(balance_after_cents, currency),
        "created_at": row["created_at"],
    }


def list_accounts(db_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Return all accounts ordered by account id."""
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(
            f"SELECT {_ACCOUNT_COLS} FROM accounts ORDER BY id ASC"
        ).fetchall()
    return [serialize_account(row) for row in rows]


def list_transfers(
    db_path: Path | str | None = None, limit: int = 25
) -> list[dict[str, Any]]:
    """Return recent transfers ordered newest first."""
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(
            f"SELECT {_TRANSFER_COLS} FROM transfers ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [serialize_transfer(row) for row in rows]


def list_ledger_entries(
    db_path: Path | str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Return recent double-entry ledger entries ordered newest first."""
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(
            f"SELECT {_LEDGER_COLS} FROM ledger_entries ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [serialize_ledger_entry(row) for row in rows]


def _safe_rollback(conn: sqlite3.Connection) -> None:
    """Roll back the active SQLite transaction if one is open."""
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def _fetch_account_or_raise(
    conn: sqlite3.Connection, account_id: str, role: str
) -> sqlite3.Row:
    """Load an account row inside an active transaction or roll back and raise."""
    row = conn.execute(
        f"SELECT {_ACCOUNT_COLS} FROM accounts WHERE id = ?",
        (account_id,),
    ).fetchone()
    if row is None:
        _safe_rollback(conn)
        raise AccountNotFoundError(f"{role} account '{account_id}' was not found.")
    return row


def _insert_transfer_record(
    conn: sqlite3.Connection,
    *,
    transfer_id: str,
    idempotency_key: str,
    from_account_id: str,
    to_account_id: str,
    source_currency: str,
    target_currency: str,
    amount_cents: int,
    target_amount_cents: int,
    usd_equivalent_cents: int,
    fx_rate: str,
    status: str,
    reason: str | None,
    velocity_flagged: int,
) -> None:
    """Insert a transfer audit row (COMPLETED or BLOCKED_FRAUD)."""
    conn.execute(
        """
        INSERT INTO transfers (
            id, idempotency_key, from_account_id, to_account_id,
            source_currency, target_currency, amount_cents, target_amount_cents,
            usd_equivalent_cents, fx_rate, status, reason, velocity_flagged
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transfer_id,
            idempotency_key,
            from_account_id,
            to_account_id,
            source_currency,
            target_currency,
            amount_cents,
            target_amount_cents,
            usd_equivalent_cents,
            fx_rate,
            status,
            reason,
            velocity_flagged,
        ),
    )


def execute_transfer(
    from_account_id: str,
    to_account_id: str,
    amount: str,
    idempotency_key: str,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Execute an atomic P2P transfer with FX conversion, fraud checks, and double-entry logging."""
    try:
        amount_cents = parse_decimal_to_minor_units(amount)
    except ValueError as exc:
        raise InvalidTransferError(str(exc)) from exc

    if not isinstance(from_account_id, str) or not from_account_id.strip():
        raise InvalidTransferError("Sender account ID is required.")
    if not isinstance(to_account_id, str) or not to_account_id.strip():
        raise InvalidTransferError("Recipient account ID is required.")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise InvalidTransferError("Idempotency-Key is required.")

    sender_id = from_account_id.strip()
    recipient_id = to_account_id.strip()
    idem_key = idempotency_key.strip()
    if sender_id == recipient_id:
        raise InvalidTransferError("Sender and recipient accounts must be different.")

    with closing(get_connection(db_path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")

            existing_row = conn.execute(
                f"SELECT {_TRANSFER_COLS} FROM transfers WHERE idempotency_key = ?",
                (idem_key,),
            ).fetchone()
            if existing_row is not None:
                _safe_rollback(conn)
                raise DuplicateIdempotencyKeyError(serialize_transfer(existing_row))

            sender_row = _fetch_account_or_raise(conn, sender_id, "Sender")
            recipient_row = _fetch_account_or_raise(conn, recipient_id, "Recipient")

            source_currency = sender_row["currency"]
            target_currency = recipient_row["currency"]

            try:
                target_amount_cents, usd_equivalent_cents, fx_rate = convert_minor_units(
                    amount_cents, source_currency, target_currency
                )
            except ValueError as exc:
                _safe_rollback(conn)
                raise InvalidTransferError(str(exc)) from exc

            if target_amount_cents <= 0 or usd_equivalent_cents <= 0:
                _safe_rollback(conn)
                raise InvalidTransferError(
                    "Transfer amount is too small after currency conversion."
                )

            transfer_id = str(uuid.uuid4())

            try:
                check_single_transfer_limit(
                    usd_equivalent_cents,
                    amount_cents=amount_cents,
                    source_currency=source_currency,
                )
            except FraudLimitExceededError as err:
                reason = getattr(err, "message", None) or str(err)
                _insert_transfer_record(
                    conn,
                    transfer_id=transfer_id,
                    idempotency_key=idem_key,
                    from_account_id=sender_id,
                    to_account_id=recipient_id,
                    source_currency=source_currency,
                    target_currency=target_currency,
                    amount_cents=amount_cents,
                    target_amount_cents=target_amount_cents,
                    usd_equivalent_cents=usd_equivalent_cents,
                    fx_rate=fx_rate,
                    status="BLOCKED_FRAUD",
                    reason=reason,
                    velocity_flagged=0,
                )
                conn.execute("COMMIT")
                raise

            sender_balance = int(sender_row["balance_cents"])
            recipient_balance = int(recipient_row["balance_cents"])
            if sender_balance < amount_cents:
                _safe_rollback(conn)
                raise InsufficientFundsError(
                    f"Insufficient funds in '{sender_id}': available "
                    f"{format_minor_units(sender_balance, source_currency)}, "
                    f"requested {format_minor_units(amount_cents, source_currency)}."
                )

            is_flagged, recent_count = check_velocity(conn, sender_id)
            flag_reason = (
                f"Velocity alert: {recent_count} transfers within {VELOCITY_WINDOW_SECONDS}s"
                if is_flagged
                else None
            )

            new_sender_balance = sender_balance - amount_cents
            new_recipient_balance = recipient_balance + target_amount_cents

            if is_flagged:
                conn.execute(
                    "UPDATE accounts SET balance_cents = ?, flagged = 1, flag_reason = ? WHERE id = ?",
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

            _insert_transfer_record(
                conn,
                transfer_id=transfer_id,
                idempotency_key=idem_key,
                from_account_id=sender_id,
                to_account_id=recipient_id,
                source_currency=source_currency,
                target_currency=target_currency,
                amount_cents=amount_cents,
                target_amount_cents=target_amount_cents,
                usd_equivalent_cents=usd_equivalent_cents,
                fx_rate=fx_rate,
                status="COMPLETED",
                reason=flag_reason,
                velocity_flagged=1 if is_flagged else 0,
            )

            conn.executemany(
                """
                INSERT INTO ledger_entries (
                    transfer_id, account_id, currency, entry_type,
                    amount_cents, balance_after_cents
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        transfer_id,
                        sender_id,
                        source_currency,
                        "DEBIT",
                        amount_cents,
                        new_sender_balance,
                    ),
                    (
                        transfer_id,
                        recipient_id,
                        target_currency,
                        "CREDIT",
                        target_amount_cents,
                        new_recipient_balance,
                    ),
                ],
            )

            transfer_row = conn.execute(
                f"SELECT {_TRANSFER_COLS} FROM transfers WHERE id = ?",
                (transfer_id,),
            ).fetchone()
            account_rows = conn.execute(
                f"SELECT {_ACCOUNT_COLS} FROM accounts ORDER BY id ASC"
            ).fetchall()

            conn.execute("COMMIT")
        except Exception:
            _safe_rollback(conn)
            raise

    return {
        "transfer": serialize_transfer(transfer_row),
        "accounts": [serialize_account(row) for row in account_rows],
        "velocity_flagged": bool(is_flagged),
        "velocity_alert": flag_reason,
    }
