"""Revenue aggregation service."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from octopus_export_optimizer.models.revenue import (
    ImportCostInterval,
    RevenueInterval,
    RevenueSummary,
)


class Aggregator:
    """Aggregates revenue intervals into day/month/rolling summaries."""

    def __init__(self, local_tz: str = "Europe/London") -> None:
        self.tz = ZoneInfo(local_tz)

    def aggregate(
        self,
        intervals: list[RevenueInterval],
        period_type: str,
        period_key: str,
        import_cost_intervals: list[ImportCostInterval] | None = None,
        battery_charge_kwh: float = 0.0,
    ) -> RevenueSummary:
        """Aggregate a list of revenue intervals into a summary.

        Optionally includes import cost intervals for net revenue
        and true profit calculation.
        """
        now = datetime.now(timezone.utc)

        if not intervals:
            # Still aggregate import costs even without export data
            import_cost = 0.0
            import_kwh = 0.0
            if import_cost_intervals:
                import_cost = sum(i.import_cost_pence for i in import_cost_intervals)
                import_kwh = sum(i.import_kwh for i in import_cost_intervals)

            # Charge cost: use avg import rate if available, else fall back
            charge_cost = self._calc_charge_cost(
                battery_charge_kwh, import_cost, import_kwh,
            )
            return RevenueSummary(
                period_type=period_type,
                period_key=period_key,
                total_export_kwh=0.0,
                agile_revenue_pence=0.0,
                flat_revenue_pence=0.0,
                uplift_pence=0.0,
                avg_realised_rate_pence=0.0,
                intervals_above_flat=0,
                total_intervals=0,
                calculated_at=now,
                import_cost_pence=round(import_cost, 4),
                total_import_kwh=round(import_kwh, 4),
                net_revenue_pence=round(-import_cost, 4),
                total_charge_kwh=round(battery_charge_kwh, 4),
                charge_cost_pence=round(charge_cost, 4),
                arbitrage_profit_pence=round(-charge_cost, 4),
            )

        total_kwh = sum(i.export_kwh for i in intervals)
        agile_rev = sum(i.agile_revenue_pence for i in intervals)
        flat_rev = sum(i.flat_revenue_pence for i in intervals)
        above_flat = sum(1 for i in intervals if i.is_uplift_positive)
        avg_rate = agile_rev / total_kwh if total_kwh > 0 else 0.0

        # Flat baseline detail: aggregate solar excess counterfactual
        flat_excess_intervals = [i for i in intervals if i.flat_export_kwh is not None]
        flat_export_kwh = (
            round(sum(i.flat_export_kwh for i in flat_excess_intervals), 4)
            if flat_excess_intervals
            else None
        )
        # Use the most common flat rate across intervals (typically one rate per period)
        flat_rates = [i.flat_rate_pence for i in intervals]
        avg_flat_rate = max(set(flat_rates), key=flat_rates.count) if flat_rates else 0.0

        # Import cost aggregation
        import_cost = 0.0
        import_kwh = 0.0
        if import_cost_intervals:
            import_cost = sum(i.import_cost_pence for i in import_cost_intervals)
            import_kwh = sum(i.import_kwh for i in import_cost_intervals)

        net_rev = agile_rev - import_cost

        charge_cost = self._calc_charge_cost(
            battery_charge_kwh, import_cost, import_kwh,
        )
        arbitrage_profit = agile_rev - charge_cost

        return RevenueSummary(
            period_type=period_type,
            period_key=period_key,
            total_export_kwh=round(total_kwh, 4),
            agile_revenue_pence=round(agile_rev, 4),
            flat_revenue_pence=round(flat_rev, 4),
            uplift_pence=round(agile_rev - flat_rev, 4),
            avg_realised_rate_pence=round(avg_rate, 2),
            intervals_above_flat=above_flat,
            total_intervals=len(intervals),
            calculated_at=now,
            flat_export_kwh=flat_export_kwh,
            avg_flat_rate_pence=round(avg_flat_rate, 2),
            import_cost_pence=round(import_cost, 4),
            total_import_kwh=round(import_kwh, 4),
            net_revenue_pence=round(net_rev, 4),
            total_charge_kwh=round(battery_charge_kwh, 4),
            charge_cost_pence=round(charge_cost, 4),
            arbitrage_profit_pence=round(arbitrage_profit, 4),
        )

    @staticmethod
    def _calc_charge_cost(
        battery_charge_kwh: float,
        import_cost_pence: float,
        import_kwh: float,
        fallback_rate_pence: float = 7.5,
    ) -> float:
        """Calculate estimated charge cost from battery_charge_kwh × avg import rate."""
        if battery_charge_kwh <= 0:
            return 0.0
        if import_kwh > 0:
            avg_rate = import_cost_pence / import_kwh
        else:
            avg_rate = fallback_rate_pence
        return battery_charge_kwh * avg_rate

    def day_boundaries(self, target_date: date) -> tuple[datetime, datetime]:
        """Get UTC start/end boundaries for a local calendar day."""
        local_start = datetime(
            target_date.year, target_date.month, target_date.day, tzinfo=self.tz
        )
        local_end = local_start + timedelta(days=1)
        return (
            local_start.astimezone(timezone.utc),
            local_end.astimezone(timezone.utc),
        )

    def month_boundaries(
        self, year: int, month: int
    ) -> tuple[datetime, datetime]:
        """Get UTC start/end boundaries for a local calendar month."""
        local_start = datetime(year, month, 1, tzinfo=self.tz)
        if month == 12:
            local_end = datetime(year + 1, 1, 1, tzinfo=self.tz)
        else:
            local_end = datetime(year, month + 1, 1, tzinfo=self.tz)
        return (
            local_start.astimezone(timezone.utc),
            local_end.astimezone(timezone.utc),
        )

    def rolling_boundaries(
        self, days: int, now: datetime | None = None
    ) -> tuple[datetime, datetime]:
        """Get UTC start/end boundaries for a rolling window."""
        if now is None:
            now = datetime.now(timezone.utc)
        return (now - timedelta(days=days), now)
