"""Real-time fraud guardrails for peer-to-peer banking transfers."""

import sqlite3

SINGLE_TRANSFER_LIMIT_USD_CENTS = 200_000
VELOCITY_MAX_TRANSFERS = 3
VELOCITY_WINDOW_SECONDS = 60


def _format_cents(cents: int) -> str:
    """Format integer cents as a comma-separated decimal string without float math."""
    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    return f"{sign}{abs_cents // 100:,}.{abs_cents % 100:02d}"


class FraudLimitExceededError(Exception):
    """Raised when a transfer's USD-equivalent value exceeds the single-transfer limit."""

    code = "FRAUD_LIMIT_EXCEEDED"

    def __init__(
        self,
        usd_equivalent_cents: int,
        amount_cents: int | None = None,
        source_currency: str = "USD",
        limit_usd_cents: int = SINGLE_TRANSFER_LIMIT_USD_CENTS,
    ) -> None:
        self.usd_equivalent_cents = usd_equivalent_cents
        self.amount_cents = (
            amount_cents if amount_cents is not None else usd_equivalent_cents
        )
        self.source_currency = source_currency
        self.limit_usd_cents = limit_usd_cents
        self.usd_equivalent_str = (
            f"{usd_equivalent_cents // 100}.{usd_equivalent_cents % 100:02d}"
        )
        self.limit_usd_str = f"{limit_usd_cents // 100}.{limit_usd_cents % 100:02d}"

        limit_display = f"${_format_cents(limit_usd_cents)}"
        if source_currency.upper() == "USD":
            amount_display = f"${_format_cents(self.amount_cents)}"
            self.message = (
                f"Transfer of {amount_display} was blocked by Fraud Guardrail "
                f"because it exceeds the {limit_display} single-transfer limit."
            )
        else:
            amount_display = f"{_format_cents(self.amount_cents)} {source_currency}"
            usd_display = f"${_format_cents(usd_equivalent_cents)}"
            self.message = (
                f"Transfer of {amount_display} ({usd_display} USD equivalent) was blocked "
                f"by Fraud Guardrail because it exceeds the {limit_display} single-transfer limit."
            )
        super().__init__(self.message)


def check_single_transfer_limit(
    usd_equivalent_cents: int,
    amount_cents: int | None = None,
    source_currency: str = "USD",
) -> None:
    """Block any transfer whose USD-equivalent value exceeds SINGLE_TRANSFER_LIMIT_USD_CENTS."""
    if usd_equivalent_cents > SINGLE_TRANSFER_LIMIT_USD_CENTS:
        raise FraudLimitExceededError(
            usd_equivalent_cents=usd_equivalent_cents,
            amount_cents=amount_cents,
            source_currency=source_currency,
            limit_usd_cents=SINGLE_TRANSFER_LIMIT_USD_CENTS,
        )


def check_velocity(conn: sqlite3.Connection, account_id: str) -> tuple[bool, int]:
    """Check whether completing another transfer exceeds the rolling-window velocity limit."""
    window_modifier = f"-{VELOCITY_WINDOW_SECONDS} seconds"
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM transfers
        WHERE from_account_id = ?
          AND status = 'COMPLETED'
          AND created_at >= datetime('now', ?)
        """,
        (account_id, window_modifier),
    ).fetchone()
    recent_count = int(row[0]) if row is not None else 0
    projected_count = recent_count + 1
    return (projected_count > VELOCITY_MAX_TRANSFERS, projected_count)
