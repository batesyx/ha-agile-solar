"""Real-time revenue estimation from HA state snapshots.

Estimates today's export revenue using feed-in power readings
and Agile tariff rates, providing live feedback before Octopus
settlement data arrives (~24h delay).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import dataclass

from octopus_export_optimizer.config.settings import ThresholdSettings
from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.models.tariff import TariffSlot

logger = logging.getLogger(__name__)


@dataclass
class RevenueEstimate:
    """Estimated revenue for a period based on HA feed-in data."""

    export_kwh: float
    agile_revenue_pence: float
    flat_revenue_pence: float
    uplift_pence: float
    snapshot_count: int

    # Import cost (Phase 2B)
    import_kwh: float = 0.0
    import_cost_pence: float = 0.0
    net_revenue_pence: float = 0.0  # agile_revenue - import_cost

    # Charging opportunity cost (Phase 3)
    charging_opportunity_cost_pence: float = 0.0
    true_profit_pence: float = 0.0  # net_revenue - opportunity_cost

    # Flat baseline (same kWh as actual export, at flat rate)
    flat_export_kwh: float = 0.0


def estimate_revenue(
    snapshots: list[HaStateSnapshot],
    tariff_slots: list[TariffSlot],
    thresholds: ThresholdSettings,
    day: datetime,
    import_tariff_slots: list[TariffSlot] | None = None,
) -> RevenueEstimate:
    """Estimate revenue from HA state snapshots and tariff rates.

    For each consecutive pair of snapshots, calculates:
        energy_kwh = feed_in_kw × hours_between_snapshots
        revenue = energy_kwh × agile_rate_for_that_period

    Also estimates import cost (grid consumption) and charging
    opportunity cost (solar used to charge battery instead of export).

    The flat baseline uses the same actual exported kWh at the flat rate,
    so uplift shows the pure rate advantage of Agile timing.

    Args:
        snapshots: Today's HA state snapshots, ordered by timestamp.
        tariff_slots: Today's export tariff slots.
        thresholds: Settings containing flat rate config.
        day: The date being estimated (for flat rate lookup).
        import_tariff_slots: Today's import tariff slots (optional).
    """
    if len(snapshots) < 2:
        return RevenueEstimate(0.0, 0.0, 0.0, 0.0, len(snapshots))

    flat_rate = thresholds.get_flat_rate_for_date(day.date() if isinstance(day, datetime) else day)

    total_kwh = 0.0
    total_agile_pence = 0.0
    total_import_kwh = 0.0
    total_import_cost = 0.0
    total_opportunity_cost = 0.0

    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]

        # Time gap in hours
        dt_seconds = (curr.timestamp - prev.timestamp).total_seconds()
        if dt_seconds <= 0 or dt_seconds > 600:
            continue

        dt_hours = dt_seconds / 3600.0

        # Find the export rate for this timestamp's half-hour slot
        export_rate = _find_rate(prev.timestamp, tariff_slots)

        # --- Export revenue ---
        feed_in_prev = max(0.0, prev.feed_in_kw or 0.0)
        feed_in_curr = max(0.0, curr.feed_in_kw or 0.0)
        avg_feed_in = (feed_in_prev + feed_in_curr) / 2.0
        export_kwh = avg_feed_in * dt_hours

        if export_kwh > 0 and export_rate is not None:
            total_kwh += export_kwh
            total_agile_pence += export_kwh * export_rate

        # --- Extract PV and load for import cost + opportunity cost ---
        pv_prev = max(0.0, prev.pv_power_kw or 0.0)
        pv_curr = max(0.0, curr.pv_power_kw or 0.0)
        load_prev = max(0.0, prev.load_power_kw or 0.0)
        load_curr = max(0.0, curr.load_power_kw or 0.0)

        # --- Import cost ---
        if import_tariff_slots:
            import_rate = _find_rate(prev.timestamp, import_tariff_slots)
            if import_rate is not None:
                bat_dis_prev = max(0.0, prev.battery_discharge_kw or 0.0)
                bat_dis_curr = max(0.0, curr.battery_discharge_kw or 0.0)
                bat_chg_prev = max(0.0, prev.battery_charge_kw or 0.0)
                bat_chg_curr = max(0.0, curr.battery_charge_kw or 0.0)

                grid_prev = max(0.0, load_prev + bat_chg_prev - pv_prev - bat_dis_prev)
                grid_curr = max(0.0, load_curr + bat_chg_curr - pv_curr - bat_dis_curr)
                avg_grid = (grid_prev + grid_curr) / 2.0
                import_kwh = avg_grid * dt_hours

                if import_kwh > 0:
                    total_import_kwh += import_kwh
                    total_import_cost += import_kwh * import_rate

        # --- Charging opportunity cost (Phase 3) ---
        if export_rate is not None and export_rate > 0:
            bat_chg_prev = max(0.0, prev.battery_charge_kw or 0.0)
            bat_chg_curr = max(0.0, curr.battery_charge_kw or 0.0)

            solar_chg_prev = min(bat_chg_prev, pv_prev)
            solar_chg_curr = min(bat_chg_curr, pv_curr)
            avg_solar_chg = (solar_chg_prev + solar_chg_curr) / 2.0
            solar_chg_kwh = avg_solar_chg * dt_hours

            if solar_chg_kwh > 0:
                total_opportunity_cost += solar_chg_kwh * export_rate

    total_flat_pence = total_kwh * flat_rate
    net_rev = total_agile_pence - total_import_cost
    true_profit = net_rev - total_opportunity_cost

    return RevenueEstimate(
        export_kwh=round(total_kwh, 4),
        agile_revenue_pence=round(total_agile_pence, 4),
        flat_revenue_pence=round(total_flat_pence, 4),
        uplift_pence=round(total_agile_pence - total_flat_pence, 4),
        snapshot_count=len(snapshots),
        import_kwh=round(total_import_kwh, 4),
        import_cost_pence=round(total_import_cost, 4),
        net_revenue_pence=round(net_rev, 4),
        charging_opportunity_cost_pence=round(total_opportunity_cost, 4),
        true_profit_pence=round(true_profit, 4),
        flat_export_kwh=round(total_kwh, 4),
    )


def _find_rate(timestamp: datetime, slots: list[TariffSlot]) -> float | None:
    """Find the tariff rate that covers the given timestamp."""
    for slot in slots:
        if slot.interval_start <= timestamp < slot.interval_end:
            return slot.rate_inc_vat_pence
    return None
