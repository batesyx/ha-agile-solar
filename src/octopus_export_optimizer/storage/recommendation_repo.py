"""Repository for recommendations and input snapshots."""

from __future__ import annotations

from datetime import datetime

from octopus_export_optimizer.models.recommendation import (
    Recommendation,
    RecommendationInputSnapshot,
)
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from octopus_export_optimizer.storage.database import Database


class RecommendationRepo:
    """CRUD operations for recommendations in SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(
        self,
        recommendation: Recommendation,
        snapshot: RecommendationInputSnapshot,
    ) -> None:
        """Save a recommendation and its input snapshot together."""
        self.db.conn.execute(
            """INSERT OR REPLACE INTO recommendation_input_snapshots
               (id, timestamp, battery_soc_pct, current_export_rate_pence,
                best_upcoming_rate_pence, best_upcoming_slot_start,
                upcoming_rates_count, current_import_rate_pence,
                solar_estimate_kw, feed_in_kw, pv_power_kw, load_power_kw,
                battery_charge_kw, battery_discharge_kw,
                remaining_generation_heuristic, exportable_battery_kwh,
                battery_headroom_kwh)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot.id,
                snapshot.timestamp.isoformat(),
                snapshot.battery_soc_pct,
                snapshot.current_export_rate_pence,
                snapshot.best_upcoming_rate_pence,
                snapshot.best_upcoming_slot_start.isoformat()
                if snapshot.best_upcoming_slot_start
                else None,
                snapshot.upcoming_rates_count,
                snapshot.current_import_rate_pence,
                snapshot.solar_estimate_kw,
                snapshot.feed_in_kw,
                snapshot.pv_power_kw,
                snapshot.load_power_kw,
                snapshot.battery_charge_kw,
                snapshot.battery_discharge_kw,
                snapshot.remaining_generation_heuristic,
                snapshot.exportable_battery_kwh,
                snapshot.battery_headroom_kwh,
            ),
        )
        self.db.conn.execute(
            """INSERT OR REPLACE INTO recommendations
               (timestamp, state, reason_code, explanation,
                battery_aware, valid_until, input_snapshot_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                recommendation.timestamp.isoformat(),
                recommendation.state.value,
                recommendation.reason_code.value,
                recommendation.explanation,
                1 if recommendation.battery_aware else 0,
                recommendation.valid_until.isoformat()
                if recommendation.valid_until
                else None,
                recommendation.input_snapshot_id,
            ),
        )
        self.db.conn.commit()

    def get_latest(self) -> Recommendation | None:
        """Get the most recent recommendation."""
        row = self.db.conn.execute(
            "SELECT * FROM recommendations ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return self._row_to_recommendation(row) if row else None

    def get_latest_with_snapshot(
        self,
    ) -> tuple[Recommendation, RecommendationInputSnapshot] | None:
        """Get the most recent recommendation with its input snapshot."""
        row = self.db.conn.execute(
            """SELECT r.*, s.battery_soc_pct, s.current_export_rate_pence,
                      s.best_upcoming_rate_pence, s.best_upcoming_slot_start,
                      s.upcoming_rates_count, s.current_import_rate_pence,
                      s.solar_estimate_kw, s.feed_in_kw, s.pv_power_kw,
                      s.load_power_kw, s.battery_charge_kw,
                      s.battery_discharge_kw, s.remaining_generation_heuristic,
                      s.exportable_battery_kwh, s.battery_headroom_kwh,
                      s.id as snapshot_id, s.timestamp as snapshot_timestamp
               FROM recommendations r
               JOIN recommendation_input_snapshots s
                 ON r.input_snapshot_id = s.id
               ORDER BY r.timestamp DESC LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        rec = self._row_to_recommendation(row)
        snapshot = RecommendationInputSnapshot(
            id=row["snapshot_id"],
            timestamp=datetime.fromisoformat(row["snapshot_timestamp"]),
            battery_soc_pct=row["battery_soc_pct"],
            current_export_rate_pence=row["current_export_rate_pence"],
            best_upcoming_rate_pence=row["best_upcoming_rate_pence"],
            best_upcoming_slot_start=datetime.fromisoformat(
                row["best_upcoming_slot_start"]
            )
            if row["best_upcoming_slot_start"]
            else None,
            upcoming_rates_count=row["upcoming_rates_count"],
            current_import_rate_pence=row["current_import_rate_pence"],
            solar_estimate_kw=row["solar_estimate_kw"],
            feed_in_kw=row["feed_in_kw"],
            pv_power_kw=row["pv_power_kw"],
            load_power_kw=row["load_power_kw"],
            battery_charge_kw=row["battery_charge_kw"],
            battery_discharge_kw=row["battery_discharge_kw"],
            remaining_generation_heuristic=row["remaining_generation_heuristic"],
            exportable_battery_kwh=row["exportable_battery_kwh"],
            battery_headroom_kwh=row["battery_headroom_kwh"],
        )
        return rec, snapshot

    @staticmethod
    def _row_to_recommendation(row: object) -> Recommendation:
        return Recommendation(
            timestamp=datetime.fromisoformat(row["timestamp"]),
            state=RecommendationState(row["state"]),
            reason_code=ReasonCode(row["reason_code"]),
            explanation=row["explanation"],
            battery_aware=bool(row["battery_aware"]),
            valid_until=datetime.fromisoformat(row["valid_until"])
            if row["valid_until"]
            else None,
            input_snapshot_id=row["input_snapshot_id"],
        )
