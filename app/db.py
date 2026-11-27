"""SQLite database path resolution and connection factory."""

import os
from pathlib import Path
import sqlite3

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "seed.db"


def get_db_path() -> Path:
    """Return the configured SQLite database path from BANK_DB_PATH or default seed.db."""
    env_path = os.environ.get("BANK_DB_PATH")
    if env_path:
        return Path(env_path)
    return _DEFAULT_DB_PATH


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection configured for explicit transactions and foreign keys."""
    target = Path(db_path) if db_path is not None else get_db_path()
    conn = sqlite3.connect(target, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
