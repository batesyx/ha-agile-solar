"""Repository for inverter command audit trail."""

from __future__ import annotations

from datetime import datetime

from octopus_export_optimizer.control.models import CommandResult
from octopus_export_optimizer.storage.database import Database


class CommandRepo:
    """CRUD operations for inverter commands in SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, record: CommandResult) -> None:
        """Persist a command result."""
        with self.db.lock:
            self.db.conn.execute(
                """INSERT OR REPLACE INTO inverter_commands
                   (id, timestamp, previous_mode, new_mode, target_max_soc,
                    target_discharge_kw,
                    recommendation_state, reason_code, success, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.timestamp.isoformat(),
                    record.previous_mode,
                    record.new_mode,
                    record.target_max_soc,
                    record.target_discharge_kw,
                    record.recommendation_state,
                    record.reason_code,
                    1 if record.success else 0,
                    record.error,
                ),
            )
            self.db.conn.commit()

    def get_latest(self) -> CommandResult | None:
        """Get the most recent command."""
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT * FROM inverter_commands ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        return self._row_to_result(row) if row else None

    def get_history(self, limit: int = 50) -> list[CommandResult]:
        """Get recent command history."""
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT * FROM inverter_commands ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_result(row) for row in rows]

    @staticmethod
    def _row_to_result(row: object) -> CommandResult:
        return CommandResult(
            id=row["id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            previous_mode=row["previous_mode"],
            new_mode=row["new_mode"],
            target_max_soc=row["target_max_soc"],
            target_discharge_kw=row["target_discharge_kw"],
            recommendation_state=row["recommendation_state"],
            reason_code=row["reason_code"],
            success=bool(row["success"]),
            error=row["error"],
        )
