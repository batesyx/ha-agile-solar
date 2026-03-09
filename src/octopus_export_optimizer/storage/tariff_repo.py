"""Repository for tariff slot data."""

from __future__ import annotations

from datetime import datetime, timedelta

from octopus_export_optimizer.models.tariff import TariffSlot
from octopus_export_optimizer.storage.database import Database


class TariffRepo:
    """CRUD operations for tariff slots in SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert_slots(self, slots: list[TariffSlot]) -> int:
        """Upsert tariff slots. Returns count of rows affected."""
        if not slots:
            return 0
        cursor = self.db.conn.cursor()
        for slot in slots:
            cursor.execute(
                """INSERT OR REPLACE INTO tariff_slots
                   (interval_start, interval_end, rate_inc_vat_pence,
                    tariff_type, product_code, provenance, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    slot.interval_start.isoformat(),
                    slot.interval_end.isoformat(),
                    slot.rate_inc_vat_pence,
                    slot.tariff_type,
                    slot.product_code,
                    slot.provenance,
                    slot.fetched_at.isoformat(),
                ),
            )
        self.db.conn.commit()
        return len(slots)

    def get_export_rates(
        self, start: datetime, end: datetime
    ) -> list[TariffSlot]:
        """Get export tariff slots within a UTC datetime range."""
        return self._get_rates("export", start, end)

    def get_import_rates(
        self, start: datetime, end: datetime
    ) -> list[TariffSlot]:
        """Get import tariff slots within a UTC datetime range."""
        return self._get_rates("import", start, end)

    def get_current_export_rate(self, now: datetime) -> TariffSlot | None:
        """Get the export tariff slot that covers the given time."""
        row = self.db.conn.execute(
            """SELECT * FROM tariff_slots
               WHERE tariff_type = 'export'
                 AND interval_start <= ?
                 AND interval_end > ?
               ORDER BY interval_start DESC LIMIT 1""",
            (now.isoformat(), now.isoformat()),
        ).fetchone()
        return self._row_to_slot(row) if row else None

    def get_current_import_rate(self, now: datetime) -> TariffSlot | None:
        """Get the import tariff slot that covers the given time."""
        row = self.db.conn.execute(
            """SELECT * FROM tariff_slots
               WHERE tariff_type = 'import'
                 AND interval_start <= ?
                 AND interval_end > ?
               ORDER BY interval_start DESC LIMIT 1""",
            (now.isoformat(), now.isoformat()),
        ).fetchone()
        return self._row_to_slot(row) if row else None

    def get_upcoming_export_rates(
        self, from_dt: datetime, hours: float
    ) -> list[TariffSlot]:
        """Get export rates from now for the next N hours."""
        end = from_dt + timedelta(hours=hours)
        return self._get_rates("export", from_dt, end)

    def get_latest_export_slot(self) -> TariffSlot | None:
        """Get the most recently fetched export tariff slot."""
        row = self.db.conn.execute(
            """SELECT * FROM tariff_slots
               WHERE tariff_type = 'export'
               ORDER BY interval_start DESC LIMIT 1"""
        ).fetchone()
        return self._row_to_slot(row) if row else None

    def _get_rates(
        self, tariff_type: str, start: datetime, end: datetime
    ) -> list[TariffSlot]:
        rows = self.db.conn.execute(
            """SELECT * FROM tariff_slots
               WHERE tariff_type = ?
                 AND interval_start >= ?
                 AND interval_start < ?
               ORDER BY interval_start""",
            (tariff_type, start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._row_to_slot(r) for r in rows]

    @staticmethod
    def _row_to_slot(row: object) -> TariffSlot:
        return TariffSlot(
            interval_start=datetime.fromisoformat(row["interval_start"]),
            interval_end=datetime.fromisoformat(row["interval_end"]),
            rate_inc_vat_pence=row["rate_inc_vat_pence"],
            tariff_type=row["tariff_type"],
            product_code=row["product_code"],
            provenance=row["provenance"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
        )
