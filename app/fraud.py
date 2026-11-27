"""Real-time fraud guardrails and velocity anomaly checks."""

import sqlite3

SINGLE_TRANSFER_LIMIT_CENTS = 200_000
VELOCITY_MAX_TRANSFERS = 3
VELOCITY_WINDOW_SECONDS = 60


class FraudLimitExceededError(Exception):
    """Raised when a transfer exceeds the single-transaction fraud threshold."""

    code = "FRAUD_LIMIT_EXCEEDED"

    def __init__(self, amount_cents: int, limit_cents: int = SINGLE_TRANSFER_LIMIT_CENTS) -> None:
        self.amount_cents = amount_cents
        self.limit_cents = limit_cents
        amount_str = f"${amount_cents // 100:,}.{amount_cents % 100:02d}"
        limit_str = f"${limit_cents // 100:,}.{limit_cents % 100:02d}"
        self.message = (
            f"Transfer of {amount_str} blocked by Fraud Guardrail: "
            f"exceeds single-transfer limit of {limit_str}."
        )
        super().__init__(self.message)


def check_single_transfer_limit(amount_cents: int) -> None:
    """Reject transfers above the single-transaction maximum."""
    if amount_cents > SINGLE_TRANSFER_LIMIT_CENTS:
        raise FraudLimitExceededError(amount_cents=amount_cents)


def check_velocity(conn: sqlite3.Connection, account_id: str) -> tuple[bool, int]:
    """Check whether completing this transfer exceeds the rolling 60s velocity threshold."""
    window_expr = f"-{VELOCITY_WINDOW_SECONDS} seconds"
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM transfers
        WHERE from_account_id = ?
          AND status = 'COMPLETED'
          AND created_at >= datetime('now', ?)
        """,
        (account_id, window_expr),
    ).fetchone()
    recent_count = int(row["cnt"]) if row is not None else 0
    projected_count = recent_count + 1
    return projected_count > VELOCITY_MAX_TRANSFERS, projected_count
