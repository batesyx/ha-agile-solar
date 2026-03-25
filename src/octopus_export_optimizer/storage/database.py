"""SQLite database connection and migration management."""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from octopus_export_optimizer.storage.migrations import (
    v001_initial,
    v002_inverter_commands,
    v003_freshness_and_import_costs,
    v004_export_planner,
    v005_solar_excess,
    v006_flat_baseline_summaries,
    v007_purge_pre_agile_data,
    v008_battery_charge,
)

logger = logging.getLogger(__name__)

MIGRATIONS = [
    (1, v001_initial),
    (2, v002_inverter_commands),
    (3, v003_freshness_and_import_costs),
    (4, v004_export_planner),
    (5, v005_solar_excess),
    (6, v006_flat_baseline_summaries),
    (7, v007_purge_pre_agile_data),
    (8, v008_battery_charge),
]


class Database:
    """SQLite database wrapper with version-tracked migrations."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self.lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def connect(self) -> None:
        """Open the database connection and run migrations."""
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False, timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._run_migrations()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _run_migrations(self) -> None:
        """Apply any pending migrations."""
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        self.conn.commit()

        current = self._current_version()
        for version, module in MIGRATIONS:
            if version > current:
                logger.info("Applying migration v%03d", version)
                module.upgrade(self.conn)
                self.conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )
                self.conn.commit()

    def _current_version(self) -> int:
        """Get the current schema version."""
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM schema_version"
        ).fetchone()
        return row["v"]

    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
