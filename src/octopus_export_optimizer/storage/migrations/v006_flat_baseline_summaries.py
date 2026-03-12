"""Add flat baseline columns to revenue_summaries — v006."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """Add flat_export_kwh and avg_flat_rate_pence to revenue_summaries."""
    for col, col_type, default in [
        ("flat_export_kwh", "REAL", None),
        ("avg_flat_rate_pence", "REAL", 0.0),
    ]:
        try:
            default_clause = f" DEFAULT {default}" if default is not None else ""
            conn.execute(
                f"ALTER TABLE revenue_summaries "
                f"ADD COLUMN {col} {col_type}{default_clause}"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
