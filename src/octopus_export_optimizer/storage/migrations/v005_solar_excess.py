"""Solar excess intervals for fair flat baseline — v005."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """Add solar_excess_intervals table and flat_export_kwh column."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS solar_excess_intervals (
            interval_start TEXT PRIMARY KEY,
            solar_excess_kwh REAL NOT NULL,
            calculated_at TEXT NOT NULL
        )"""
    )

    try:
        conn.execute(
            "ALTER TABLE revenue_intervals ADD COLUMN flat_export_kwh REAL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
