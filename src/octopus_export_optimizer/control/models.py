"""Data models for inverter control operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CommandResult(BaseModel):
    """Result of an inverter control command."""

    id: str
    timestamp: datetime
    previous_mode: str | None
    new_mode: str
    target_max_soc: int | None = None
    recommendation_state: str
    reason_code: str
    success: bool
    error: str | None = None
