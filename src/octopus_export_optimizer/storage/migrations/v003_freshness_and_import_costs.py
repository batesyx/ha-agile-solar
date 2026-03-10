"""Data freshness tracking and import cost tables — v003."""

from __future__ import annotations

import sqlite3


def upgrade(conn: sqlite3.Connection) -> None:
    """Add tariff_data_age_minutes to snapshots and create import cost table."""
    # Freshness tracking on recommendation snapshots
    try:
        conn.execute(
            "ALTER TABLE recommendation_input_snapshots "
            "ADD COLUMN tariff_data_age_minutes REAL"
        )
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Import cost intervals (mirrors revenue_intervals for import side)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS import_cost_intervals (
            interval_start TEXT NOT NULL PRIMARY KEY,
            import_kwh REAL NOT NULL,
            import_rate_pence REAL NOT NULL,
            import_cost_pence REAL NOT NULL,
            calculated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_import_cost_intervals_start
            ON import_cost_intervals(interval_start);
    """)

    # Extend revenue_summaries with import cost and net revenue columns
    for col, default in [
        ("import_cost_pence", 0.0),
        ("total_import_kwh", 0.0),
        ("net_revenue_pence", 0.0),
        ("charging_opportunity_cost_pence", 0.0),
        ("true_profit_pence", 0.0),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE revenue_summaries "
                f"ADD COLUMN {col} REAL DEFAULT {default}"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
