"""Meter interval domain model."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class MeterInterval(BaseModel):
    """A single half-hour metered energy interval.

    Settlement-grade data from the Octopus Energy API.
    Typically delayed by ~24 hours.
    """

    interval_start: datetime  # UTC
    interval_end: datetime  # UTC
    kwh: float
    direction: Literal["export", "import"]
    fetched_at: datetime
