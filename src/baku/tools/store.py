"""Local SQLite storage for reminders and finances. Stays on the device."""
from __future__ import annotations

import sqlite3

from ..config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    due TEXT NOT NULL,          -- ISO 8601 local time
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,          -- YYYY-MM-DD
    kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    amount REAL NOT NULL CHECK (amount > 0),
    category TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir() / "baku.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
