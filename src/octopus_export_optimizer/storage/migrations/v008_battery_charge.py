"""Add battery charge tracking columns to revenue_summaries — v008."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """Add total_charge_kwh, charge_cost_pence, arbitrage_profit_pence."""
    for col, col_type, default in [
        ("total_charge_kwh", "REAL", 0.0),
        ("charge_cost_pence", "REAL", 0.0),
        ("arbitrage_profit_pence", "REAL", 0.0),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE revenue_summaries "
                f"ADD COLUMN {col} {col_type} DEFAULT {default}"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
