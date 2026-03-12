"""Purge pre-Agile revenue data — v007.

Revenue intervals and summaries calculated before the user's actual
Agile export start date (2026-03-10) used market rates that didn't
apply, inflating month/rolling totals.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

AGILE_START = "2026-03-10T00:00:00"


def upgrade(conn: sqlite3.Connection) -> None:
    """Delete revenue data from before Agile export start date."""
    cur = conn.execute(
        "DELETE FROM revenue_intervals WHERE interval_start < ?",
        (AGILE_START,),
    )
    logger.info("Purged %d pre-Agile revenue intervals", cur.rowcount)

    cur = conn.execute(
        "DELETE FROM import_cost_intervals WHERE interval_start < ?",
        (AGILE_START,),
    )
    logger.info("Purged %d pre-Agile import cost intervals", cur.rowcount)

    # Clear stale summaries so they get recalculated from clean data
    conn.execute("DELETE FROM revenue_summaries")
    logger.info("Cleared all revenue summaries for recalculation")
