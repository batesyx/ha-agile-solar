"""Add counterfactual Agile estimate columns to revenue_summaries — v009."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """Add agile_estimate_pence and agile_estimate_slots."""
    for col, col_type, default in [
        ("agile_estimate_pence", "REAL", 0.0),
        ("agile_estimate_slots", "INTEGER", 0),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE revenue_summaries "
                f"ADD COLUMN {col} {col_type} DEFAULT {default}"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
