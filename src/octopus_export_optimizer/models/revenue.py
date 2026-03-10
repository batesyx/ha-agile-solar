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


class ImportCostInterval(BaseModel):
    """Import cost for a single half-hour interval.

    Joins metered import data with the actual import tariff rate.
    """

    interval_start: datetime  # UTC
    import_kwh: float
    import_rate_pence: float
    import_cost_pence: float
    calculated_at: datetime


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

    # Import cost fields (Phase 2A)
    import_cost_pence: float = 0.0
    total_import_kwh: float = 0.0
    net_revenue_pence: float = 0.0  # agile_revenue - import_cost

    # Charging opportunity cost (Phase 3)
    charging_opportunity_cost_pence: float = 0.0
    true_profit_pence: float = 0.0  # net_revenue - opportunity_cost

    @property
    def agile_revenue_gbp(self) -> float:
        return self.agile_revenue_pence / 100.0

    @property
    def flat_revenue_gbp(self) -> float:
        return self.flat_revenue_pence / 100.0

    @property
    def uplift_gbp(self) -> float:
        return self.uplift_pence / 100.0

    @property
    def net_revenue_gbp(self) -> float:
        return self.net_revenue_pence / 100.0

    @property
    def true_profit_gbp(self) -> float:
        return self.true_profit_pence / 100.0
