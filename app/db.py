"""Database connection helpers for SQLite persistence."""

import os
from pathlib import Path
import sqlite3

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "seed.db"


def get_db_path() -> Path:
    """Return the configured SQLite database path."""
    override = os.environ.get("BANK_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection configured for explicit transactions and foreign keys."""
    target = db_path if db_path is not None else get_db_path()
    conn = sqlite3.connect(target, isolation_level=None, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
