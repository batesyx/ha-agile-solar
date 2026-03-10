"""Repository for revenue intervals and summaries."""

from __future__ import annotations

from datetime import datetime

from octopus_export_optimizer.models.revenue import (
    ImportCostInterval,
    RevenueInterval,
    RevenueSummary,
)
from octopus_export_optimizer.storage.database import Database


class RevenueRepo:
    """CRUD operations for revenue data in SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_intervals(self, intervals: list[RevenueInterval]) -> int:
        """Upsert revenue intervals. Returns count of rows affected."""
        if not intervals:
            return 0
        with self.db.lock:
            cursor = self.db.conn.cursor()
            for interval in intervals:
                cursor.execute(
                    """INSERT OR REPLACE INTO revenue_intervals
                       (interval_start, export_kwh, agile_rate_pence,
                        agile_revenue_pence, flat_rate_pence, flat_revenue_pence,
                        uplift_pence, calculated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        interval.interval_start.isoformat(),
                        interval.export_kwh,
                        interval.agile_rate_pence,
                        interval.agile_revenue_pence,
                        interval.flat_rate_pence,
                        interval.flat_revenue_pence,
                        interval.uplift_pence,
                        interval.calculated_at.isoformat(),
                    ),
                )
            self.db.conn.commit()
        return len(intervals)

    def get_intervals(
        self, start: datetime, end: datetime
    ) -> list[RevenueInterval]:
        """Get revenue intervals within a UTC datetime range."""
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT * FROM revenue_intervals
                   WHERE interval_start >= ? AND interval_start < ?
                   ORDER BY interval_start""",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [self._row_to_interval(r) for r in rows]

    def upsert_import_cost_intervals(
        self, intervals: list[ImportCostInterval]
    ) -> int:
        """Upsert import cost intervals. Returns count of rows affected."""
        if not intervals:
            return 0
        with self.db.lock:
            cursor = self.db.conn.cursor()
            for interval in intervals:
                cursor.execute(
                    """INSERT OR REPLACE INTO import_cost_intervals
                       (interval_start, import_kwh, import_rate_pence,
                        import_cost_pence, calculated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        interval.interval_start.isoformat(),
                        interval.import_kwh,
                        interval.import_rate_pence,
                        interval.import_cost_pence,
                        interval.calculated_at.isoformat(),
                    ),
                )
            self.db.conn.commit()
        return len(intervals)

    def get_import_cost_intervals(
        self, start: datetime, end: datetime
    ) -> list[ImportCostInterval]:
        """Get import cost intervals within a UTC datetime range."""
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT * FROM import_cost_intervals
                   WHERE interval_start >= ? AND interval_start < ?
                   ORDER BY interval_start""",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [self._row_to_import_cost(r) for r in rows]

    def upsert_summary(self, summary: RevenueSummary) -> None:
        """Upsert a revenue summary."""
        with self.db.lock:
            self.db.conn.execute(
                """INSERT OR REPLACE INTO revenue_summaries
                   (period_type, period_key, total_export_kwh,
                    agile_revenue_pence, flat_revenue_pence, uplift_pence,
                    avg_realised_rate_pence, intervals_above_flat,
                    total_intervals, calculated_at,
                    import_cost_pence, total_import_kwh, net_revenue_pence,
                    charging_opportunity_cost_pence, true_profit_pence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    summary.period_type,
                    summary.period_key,
                    summary.total_export_kwh,
                    summary.agile_revenue_pence,
                    summary.flat_revenue_pence,
                    summary.uplift_pence,
                    summary.avg_realised_rate_pence,
                    summary.intervals_above_flat,
                    summary.total_intervals,
                    summary.calculated_at.isoformat(),
                    summary.import_cost_pence,
                    summary.total_import_kwh,
                    summary.net_revenue_pence,
                    summary.charging_opportunity_cost_pence,
                    summary.true_profit_pence,
                ),
            )
            self.db.conn.commit()

    def get_summary(
        self, period_type: str, period_key: str
    ) -> RevenueSummary | None:
        """Get a specific revenue summary."""
        with self.db.lock:
            row = self.db.conn.execute(
                """SELECT * FROM revenue_summaries
                   WHERE period_type = ? AND period_key = ?""",
                (period_type, period_key),
            ).fetchone()
        return self._row_to_summary(row) if row else None

    @staticmethod
    def _row_to_interval(row: object) -> RevenueInterval:
        return RevenueInterval(
            interval_start=datetime.fromisoformat(row["interval_start"]),
            export_kwh=row["export_kwh"],
            agile_rate_pence=row["agile_rate_pence"],
            agile_revenue_pence=row["agile_revenue_pence"],
            flat_rate_pence=row["flat_rate_pence"],
            flat_revenue_pence=row["flat_revenue_pence"],
            uplift_pence=row["uplift_pence"],
            calculated_at=datetime.fromisoformat(row["calculated_at"]),
        )

    @staticmethod
    def _row_to_import_cost(row: object) -> ImportCostInterval:
        return ImportCostInterval(
            interval_start=datetime.fromisoformat(row["interval_start"]),
            import_kwh=row["import_kwh"],
            import_rate_pence=row["import_rate_pence"],
            import_cost_pence=row["import_cost_pence"],
            calculated_at=datetime.fromisoformat(row["calculated_at"]),
        )

    @staticmethod
    def _row_to_summary(row: object) -> RevenueSummary:
        return RevenueSummary(
            period_type=row["period_type"],
            period_key=row["period_key"],
            total_export_kwh=row["total_export_kwh"],
            agile_revenue_pence=row["agile_revenue_pence"],
            flat_revenue_pence=row["flat_revenue_pence"],
            uplift_pence=row["uplift_pence"],
            avg_realised_rate_pence=row["avg_realised_rate_pence"],
            intervals_above_flat=row["intervals_above_flat"],
            total_intervals=row["total_intervals"],
            calculated_at=datetime.fromisoformat(row["calculated_at"]),
            import_cost_pence=row["import_cost_pence"] or 0.0,
            total_import_kwh=row["total_import_kwh"] or 0.0,
            net_revenue_pence=row["net_revenue_pence"] or 0.0,
            charging_opportunity_cost_pence=row["charging_opportunity_cost_pence"] or 0.0,
            true_profit_pence=row["true_profit_pence"] or 0.0,
        )
