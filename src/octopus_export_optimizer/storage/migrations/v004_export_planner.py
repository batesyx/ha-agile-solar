"""Export planner support — v004."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """Add target_discharge_kw column to inverter_commands."""
    try:
        conn.execute(
            "ALTER TABLE inverter_commands ADD COLUMN target_discharge_kw REAL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists
