"""Initialize and seed the SQLite database for the P2P banking demo."""

from contextlib import closing
from pathlib import Path
import sqlite3

DEFAULT_DB_PATH = Path(__file__).parent / "seed.db"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    currency TEXT NOT NULL DEFAULT 'USD',
    balance_cents INTEGER NOT NULL CHECK (balance_cents >= 0),
    flagged INTEGER NOT NULL DEFAULT 0,
    flag_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transfers (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    from_account_id TEXT NOT NULL REFERENCES accounts(id),
    to_account_id TEXT NOT NULL REFERENCES accounts(id),
    source_currency TEXT NOT NULL DEFAULT 'USD',
    target_currency TEXT NOT NULL DEFAULT 'USD',
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    target_amount_cents INTEGER NOT NULL CHECK (target_amount_cents > 0),
    usd_equivalent_cents INTEGER NOT NULL CHECK (usd_equivalent_cents > 0),
    fx_rate TEXT NOT NULL DEFAULT '1.000000',
    status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'BLOCKED_FRAUD', 'REJECTED')),
    reason TEXT,
    velocity_flagged INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transfer_id TEXT NOT NULL REFERENCES transfers(id),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    currency TEXT NOT NULL DEFAULT 'USD',
    entry_type TEXT NOT NULL CHECK (entry_type IN ('DEBIT', 'CREDIT')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    balance_after_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SEED_ACCOUNTS = [
    ("alice", "Alice", "USD", 500000, 0, None),
    ("bob", "Bob", "USD", 120000, 0, None),
    ("clara", "Clara", "EUR", 250000, 0, None),
]

CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "PHP": "₱",
}


def _remove_db_files(path: Path) -> None:
    """Remove an existing SQLite database file and any WAL/SHM/journal sidecars."""
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    ):
        if candidate.exists():
            candidate.unlink()


def init_db(db_path: Path | str = DEFAULT_DB_PATH, reset: bool = True) -> None:
    """Create schema tables and seed initial multi-currency accounts."""
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if reset:
        _remove_db_files(target)

    with closing(sqlite3.connect(target)) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(SCHEMA_SQL)
        conn.execute("PRAGMA foreign_keys = ON;")
        for account_id, name, currency, balance_cents, flagged, flag_reason in SEED_ACCOUNTS:
            conn.execute(
                """
                INSERT OR IGNORE INTO accounts (id, name, currency, balance_cents, flagged, flag_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (account_id, name, currency, balance_cents, flagged, flag_reason),
            )
        conn.commit()


if __name__ == "__main__":
    init_db(reset=True)
    with closing(sqlite3.connect(DEFAULT_DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT id, name, currency, balance_cents, flagged FROM accounts ORDER BY id"
        ).fetchall()
    print(f"Seeded {DEFAULT_DB_PATH}:")
    for account_id, name, currency, balance_cents, flagged in rows:
        symbol = CURRENCY_SYMBOLS.get(currency, "")
        formatted = f"{symbol}{balance_cents // 100:,}.{balance_cents % 100:02d}"
        print(
            f"  - {name} ({account_id}, {currency}): {formatted} "
            f"({balance_cents} minor units), flagged={flagged}"
        )
