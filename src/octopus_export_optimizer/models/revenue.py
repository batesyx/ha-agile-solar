"""Revenue calculation domain models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RevenueInterval(BaseModel):
    """Revenue calculation for a single half-hour interval.

    Joins metered export data with the actual tariff rate and
    the counterfactual flat rate for that date.
    """

    interval_start: datetime  # UTC
    export_kwh: float
    agile_rate_pence: float
    agile_revenue_pence: float
    flat_rate_pence: float
    flat_revenue_pence: float
    uplift_pence: float  # agile_revenue - flat_revenue
    calculated_at: datetime

    @property
    def is_uplift_positive(self) -> bool:
        """Whether Agile outperformed the flat rate for this interval."""
        return self.uplift_pence > 0


class RevenueSummary(BaseModel):
    """Aggregated revenue metrics for a time period."""

    period_type: Literal["day", "month", "rolling_7d", "rolling_30d"]
    period_key: str  # e.g. "2026-03-09" or "2026-03"
    total_export_kwh: float
    agile_revenue_pence: float
    flat_revenue_pence: float
    uplift_pence: float
    avg_realised_rate_pence: float  # agile_revenue / export_kwh
    intervals_above_flat: int
    total_intervals: int
    calculated_at: datetime

    @property
    def agile_revenue_gbp(self) -> float:
        return self.agile_revenue_pence / 100.0

    @property
    def flat_revenue_gbp(self) -> float:
        return self.flat_revenue_pence / 100.0

    @property
    def uplift_gbp(self) -> float:
        return self.uplift_pence / 100.0
