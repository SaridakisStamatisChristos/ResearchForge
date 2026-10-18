"""SQLite WAL configuration and conservative schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SchemaVersionError(Exception):
    """Unsupported or corrupt ledger schema."""


def open_ledger_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, 1):
            raise SchemaVersionError(f"Unsupported ledger version {version}")
        if version == 0:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS events (
                       campaign_id TEXT NOT NULL,
                       sequence INTEGER NOT NULL CHECK(sequence >= 1),
                       event_id TEXT NOT NULL UNIQUE,
                       event_json TEXT NOT NULL,
                       payload_sha256 TEXT NOT NULL,
                       event_sha256 TEXT NOT NULL,
                       PRIMARY KEY(campaign_id, sequence)
                    )"""
                )
                connection.execute("PRAGMA user_version=1")
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        # Normal API clients can only append; privileged owners can still bypass triggers.
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS events_no_update
               BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append-only events'); END"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS events_no_delete
               BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append-only events'); END"""
        )
        return connection
    except BaseException:
        connection.close()
        raise
