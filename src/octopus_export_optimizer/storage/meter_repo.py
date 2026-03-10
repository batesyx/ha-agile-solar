"""Repository for meter interval data."""

from __future__ import annotations

from datetime import datetime

from octopus_export_optimizer.models.meter import MeterInterval
from octopus_export_optimizer.storage.database import Database


class MeterRepo:
    """CRUD operations for meter intervals in SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_intervals(self, intervals: list[MeterInterval]) -> int:
        """Upsert meter intervals. Returns count of rows affected."""
        if not intervals:
            return 0
        with self.db.lock:
            cursor = self.db.conn.cursor()
            for interval in intervals:
                cursor.execute(
                    """INSERT OR REPLACE INTO meter_intervals
                       (interval_start, interval_end, kwh, direction, fetched_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        interval.interval_start.isoformat(),
                        interval.interval_end.isoformat(),
                        interval.kwh,
                        interval.direction,
                        interval.fetched_at.isoformat(),
                    ),
                )
            self.db.conn.commit()
        return len(intervals)

    def get_export_intervals(
        self, start: datetime, end: datetime
    ) -> list[MeterInterval]:
        """Get export meter intervals within a UTC datetime range."""
        return self._get_intervals("export", start, end)

    def get_import_intervals(
        self, start: datetime, end: datetime
    ) -> list[MeterInterval]:
        """Get import meter intervals within a UTC datetime range."""
        return self._get_intervals("import", start, end)

    def get_latest_export_interval(self) -> MeterInterval | None:
        """Get the most recent export interval."""
        with self.db.lock:
            row = self.db.conn.execute(
                """SELECT * FROM meter_intervals
                   WHERE direction = 'export'
                   ORDER BY interval_start DESC LIMIT 1"""
            ).fetchone()
        return self._row_to_interval(row) if row else None

    def _get_intervals(
        self, direction: str, start: datetime, end: datetime
    ) -> list[MeterInterval]:
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT * FROM meter_intervals
                   WHERE direction = ?
                     AND interval_start >= ?
                     AND interval_start < ?
                   ORDER BY interval_start""",
                (direction, start.isoformat(), end.isoformat()),
            ).fetchall()
        return [self._row_to_interval(r) for r in rows]

    @staticmethod
    def _row_to_interval(row: object) -> MeterInterval:
        return MeterInterval(
            interval_start=datetime.fromisoformat(row["interval_start"]),
            interval_end=datetime.fromisoformat(row["interval_end"]),
            kwh=row["kwh"],
            direction=row["direction"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )
