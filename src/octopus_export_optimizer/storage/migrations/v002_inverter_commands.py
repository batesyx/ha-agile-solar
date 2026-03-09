"""Inverter command audit trail — v002."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """Create the inverter_commands table and add target_max_soc to recommendations."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inverter_commands (
            id TEXT NOT NULL PRIMARY KEY,
            timestamp TEXT NOT NULL,
            previous_mode TEXT,
            new_mode TEXT NOT NULL,
            target_max_soc INTEGER,
            recommendation_state TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            success INTEGER NOT NULL DEFAULT 1,
            error TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_inverter_commands_timestamp
            ON inverter_commands(timestamp);
    """)
    # Add target_max_soc column to existing recommendations table
    try:
        conn.execute("ALTER TABLE recommendations ADD COLUMN target_max_soc INTEGER")
    except sqlite3.OperationalError:
        pass  # Column already exists
