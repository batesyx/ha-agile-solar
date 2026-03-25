"""Repository for revenue intervals and summaries."""

from __future__ import annotations

from datetime import datetime

from octopus_export_optimizer.models.revenue import (
    ImportCostInterval,
    RevenueInterval,
    RevenueSummary,
)
from octopus_export_optimizer.storage.database import Database


def _safe_col(row: object, col: str, default: float = 0.0) -> float:
    """Safely read a column that may not exist in pre-migration rows."""
    try:
        val = row[col]
        return val if val is not None else default
    except (IndexError, KeyError):
        return default


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
                        uplift_pence, calculated_at, flat_export_kwh)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        interval.interval_start.isoformat(),
                        interval.export_kwh,
                        interval.agile_rate_pence,
                        interval.agile_revenue_pence,
                        interval.flat_rate_pence,
                        interval.flat_revenue_pence,
                        interval.uplift_pence,
                        interval.calculated_at.isoformat(),
                        interval.flat_export_kwh,
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
                    charging_opportunity_cost_pence, true_profit_pence,
                    flat_export_kwh, avg_flat_rate_pence,
                    total_charge_kwh, charge_cost_pence, arbitrage_profit_pence,
                    agile_estimate_pence, agile_estimate_slots)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    summary.flat_export_kwh,
                    summary.avg_flat_rate_pence,
                    summary.total_charge_kwh,
                    summary.charge_cost_pence,
                    summary.arbitrage_profit_pence,
                    summary.agile_estimate_pence,
                    summary.agile_estimate_slots,
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

    def get_daily_summaries(self, days: int = 30) -> list[dict]:
        """Get recent daily revenue summaries for charting."""
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT period_key, total_export_kwh,
                          agile_revenue_pence, flat_revenue_pence,
                          uplift_pence, avg_realised_rate_pence,
                          net_revenue_pence,
                          total_charge_kwh, charge_cost_pence,
                          arbitrage_profit_pence,
                          agile_estimate_pence, agile_estimate_slots
                   FROM revenue_summaries
                   WHERE period_type = 'day'
                   ORDER BY period_key DESC
                   LIMIT ?""",
                (days,),
            ).fetchall()
        return [
            {
                "date": r[0],
                "export_kwh": r[1],
                "agile_pence": r[2],
                "flat_pence": r[3],
                "uplift_pence": r[4],
                "avg_rate": r[5],
                "net_pence": r[6],
                "battery_charge_kwh": r[7] or 0.0,
                "charge_cost_pence": r[8] or 0.0,
                "arbitrage_profit_pence": r[9] or 0.0,
                "agile_estimate_pence": r[10] or 0.0,
                "agile_estimate_slots": r[11] or 0,
            }
            for r in reversed(rows)
        ]

    def get_monthly_summaries(self, months: int = 12) -> list[dict]:
        """Get recent monthly revenue summaries for charting."""
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT period_key, total_export_kwh,
                          agile_revenue_pence, flat_revenue_pence,
                          uplift_pence, avg_realised_rate_pence,
                          net_revenue_pence, import_cost_pence,
                          total_import_kwh
                   FROM revenue_summaries
                   WHERE period_type = 'month'
                   ORDER BY period_key DESC
                   LIMIT ?""",
                (months,),
            ).fetchall()
        return [
            {
                "month": r[0],
                "export_kwh": r[1],
                "agile_pence": r[2],
                "flat_pence": r[3],
                "uplift_pence": r[4],
                "avg_rate": r[5],
                "net_pence": r[6],
                "import_cost_pence": r[7],
                "import_kwh": r[8],
            }
            for r in reversed(rows)
        ]

    @staticmethod
    def _row_to_interval(row: object) -> RevenueInterval:
        # flat_export_kwh may be NULL for pre-migration rows
        flat_export_kwh = None
        try:
            flat_export_kwh = row["flat_export_kwh"]
        except (IndexError, KeyError):
            pass
        return RevenueInterval(
            interval_start=datetime.fromisoformat(row["interval_start"]),
            export_kwh=row["export_kwh"],
            agile_rate_pence=row["agile_rate_pence"],
            agile_revenue_pence=row["agile_revenue_pence"],
            flat_rate_pence=row["flat_rate_pence"],
            flat_revenue_pence=row["flat_revenue_pence"],
            uplift_pence=row["uplift_pence"],
            calculated_at=datetime.fromisoformat(row["calculated_at"]),
            flat_export_kwh=flat_export_kwh,
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
        # flat_export_kwh may be NULL for pre-migration rows
        flat_export_kwh = None
        try:
            flat_export_kwh = row["flat_export_kwh"]
        except (IndexError, KeyError):
            pass
        avg_flat_rate = 0.0
        try:
            avg_flat_rate = row["avg_flat_rate_pence"] or 0.0
        except (IndexError, KeyError):
            pass
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
            flat_export_kwh=flat_export_kwh,
            avg_flat_rate_pence=avg_flat_rate,
            total_charge_kwh=_safe_col(row, "total_charge_kwh", 0.0),
            charge_cost_pence=_safe_col(row, "charge_cost_pence", 0.0),
            arbitrage_profit_pence=_safe_col(row, "arbitrage_profit_pence", 0.0),
            agile_estimate_pence=_safe_col(row, "agile_estimate_pence", 0.0),
            agile_estimate_slots=int(_safe_col(row, "agile_estimate_slots", 0)),
        )
