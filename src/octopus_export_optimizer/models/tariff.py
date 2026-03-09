"""Tariff slot domain model."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TariffSlot(BaseModel):
    """A single half-hour tariff interval.

    Represents the unit rate for a 30-minute settlement period,
    as published by Octopus Energy.
    """

    interval_start: datetime  # UTC
    interval_end: datetime  # UTC
    rate_inc_vat_pence: float
    tariff_type: Literal["export", "import"]
    product_code: str
    provenance: Literal["actual", "published", "forecast"] = "published"
    fetched_at: datetime

    @property
    def rate_gbp(self) -> float:
        """Rate in GBP per kWh."""
        return self.rate_inc_vat_pence / 100.0

    @property
    def is_negative(self) -> bool:
        """Whether the rate is negative (Agile export can go negative)."""
        return self.rate_inc_vat_pence < 0
