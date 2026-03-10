"""Repository for Home Assistant state snapshots."""

from __future__ import annotations

from datetime import datetime

from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.storage.database import Database


class HaStateRepo:
    """CRUD operations for HA state snapshots in SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def insert(self, snapshot: HaStateSnapshot) -> None:
        """Insert a new state snapshot."""
        with self.db.lock:
            self.db.conn.execute(
                """INSERT OR REPLACE INTO ha_state_snapshots
                   (timestamp, battery_soc_pct, pv_power_kw, feed_in_kw,
                    load_power_kw, grid_consumption_kw, battery_charge_kw,
                    battery_discharge_kw, work_mode, max_soc, min_soc,
                    force_charge_power_kw, force_discharge_power_kw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot.timestamp.isoformat(),
                    snapshot.battery_soc_pct,
                    snapshot.pv_power_kw,
                    snapshot.feed_in_kw,
                    snapshot.load_power_kw,
                    snapshot.grid_consumption_kw,
                    snapshot.battery_charge_kw,
                    snapshot.battery_discharge_kw,
                    snapshot.work_mode,
                    snapshot.max_soc,
                    snapshot.min_soc,
                    snapshot.force_charge_power_kw,
                    snapshot.force_discharge_power_kw,
                ),
            )
            self.db.conn.commit()

    def get_latest(self) -> HaStateSnapshot | None:
        """Get the most recent state snapshot."""
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT * FROM ha_state_snapshots ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return self._row_to_snapshot(row) if row else None

    def get_by_range(
        self, start: datetime, end: datetime
    ) -> list[HaStateSnapshot]:
        """Get snapshots within a UTC datetime range."""
        with self.db.lock:
            rows = self.db.conn.execute(
                """SELECT * FROM ha_state_snapshots
                   WHERE timestamp >= ? AND timestamp < ?
                   ORDER BY timestamp""",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    def delete_before(self, cutoff: datetime) -> int:
        """Delete snapshots older than cutoff. Returns deleted count."""
        with self.db.lock:
            cursor = self.db.conn.execute(
                "DELETE FROM ha_state_snapshots WHERE timestamp < ?",
                (cutoff.isoformat(),),
            )
            self.db.conn.commit()
            count = cursor.rowcount
        return count

    @staticmethod
    def _row_to_snapshot(row: object) -> HaStateSnapshot:
        return HaStateSnapshot(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            battery_soc_pct=row["battery_soc_pct"],
            pv_power_kw=row["pv_power_kw"],
            feed_in_kw=row["feed_in_kw"],
            load_power_kw=row["load_power_kw"],
            grid_consumption_kw=row["grid_consumption_kw"],
            battery_charge_kw=row["battery_charge_kw"],
            battery_discharge_kw=row["battery_discharge_kw"],
            work_mode=row["work_mode"],
            max_soc=row["max_soc"],
            min_soc=row["min_soc"],
            force_charge_power_kw=row["force_charge_power_kw"],
            force_discharge_power_kw=row["force_discharge_power_kw"],
        )
