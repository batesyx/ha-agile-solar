"""Repository for job run tracking."""

from __future__ import annotations

from datetime import datetime

from octopus_export_optimizer.models.job import JobRun
from octopus_export_optimizer.storage.database import Database


class JobRepo:
    """CRUD operations for job runs in SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, job: JobRun) -> None:
        """Insert or update a job run record."""
        with self.db.lock:
            self.db.conn.execute(
                """INSERT OR REPLACE INTO job_runs
                   (id, job_type, started_at, finished_at, status,
                    records_processed, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.id,
                    job.job_type,
                    job.started_at.isoformat(),
                    job.finished_at.isoformat() if job.finished_at else None,
                    job.status,
                    job.records_processed,
                    job.error_message,
                ),
            )
            self.db.conn.commit()

    def get_latest_by_type(self, job_type: str) -> JobRun | None:
        """Get the most recent run of a specific job type."""
        with self.db.lock:
            row = self.db.conn.execute(
                """SELECT * FROM job_runs
                   WHERE job_type = ?
                   ORDER BY started_at DESC LIMIT 1""",
                (job_type,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def get_recent(self, limit: int = 20) -> list[JobRun]:
        """Get recent job runs across all types."""
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT * FROM job_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(row: object) -> JobRun:
        return JobRun(
            id=row["id"],
            job_type=row["job_type"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"])
            if row["finished_at"]
            else None,
            status=row["status"],
            records_processed=row["records_processed"],
            error_message=row["error_message"],
        )
