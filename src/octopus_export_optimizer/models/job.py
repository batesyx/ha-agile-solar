"""Job run tracking domain model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class JobRun(BaseModel):
    """Tracks execution of a scheduled job."""

    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex)
    job_type: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["running", "success", "failed"] = "running"
    records_processed: int = 0
    error_message: str | None = None

    def complete(self, records: int = 0) -> None:
        """Mark the job as successfully completed."""
        self.finished_at = datetime.now(timezone.utc)
        self.status = "success"
        self.records_processed = records

    def fail(self, error: str) -> None:
        """Mark the job as failed."""
        self.finished_at = datetime.now(timezone.utc)
        self.status = "failed"
        self.error_message = error
