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


def estimate_revenue(
    snapshots: list[HaStateSnapshot],
    tariff_slots: list[TariffSlot],
    thresholds: ThresholdSettings,
    day: datetime,
) -> RevenueEstimate:
    """Estimate revenue from HA state snapshots and tariff rates.

    For each consecutive pair of snapshots, calculates:
        energy_kwh = feed_in_kw × hours_between_snapshots
        revenue = energy_kwh × agile_rate_for_that_period

    Args:
        snapshots: Today's HA state snapshots, ordered by timestamp.
        tariff_slots: Today's export tariff slots.
        thresholds: Settings containing flat rate config.
        day: The date being estimated (for flat rate lookup).
    """
    if len(snapshots) < 2:
        return RevenueEstimate(0.0, 0.0, 0.0, 0.0, len(snapshots))

    # Build a lookup: interval_start → rate
    rate_map: dict[datetime, float] = {}
    for slot in tariff_slots:
        rate_map[slot.interval_start] = slot.rate_inc_vat_pence

    flat_rate = thresholds.get_flat_rate_for_date(day.date() if isinstance(day, datetime) else day)

    total_kwh = 0.0
    total_agile_pence = 0.0

    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]
        curr = snapshots[i]

        # Time gap in hours
        dt_seconds = (curr.timestamp - prev.timestamp).total_seconds()
        if dt_seconds <= 0 or dt_seconds > 600:
            # Skip gaps > 10 minutes (service was down) or negative
            continue

        dt_hours = dt_seconds / 3600.0

        # Average feed-in power over the interval
        feed_in_prev = max(0.0, prev.feed_in_kw or 0.0)
        feed_in_curr = max(0.0, curr.feed_in_kw or 0.0)
        avg_feed_in = (feed_in_prev + feed_in_curr) / 2.0

        energy_kwh = avg_feed_in * dt_hours

        if energy_kwh <= 0:
            continue

        # Find the Agile rate for this timestamp's half-hour slot
        rate = _find_rate(prev.timestamp, tariff_slots)
        if rate is None:
            continue

        total_kwh += energy_kwh
        total_agile_pence += energy_kwh * rate

    total_flat_pence = total_kwh * flat_rate

    return RevenueEstimate(
        export_kwh=round(total_kwh, 4),
        agile_revenue_pence=round(total_agile_pence, 4),
        flat_revenue_pence=round(total_flat_pence, 4),
        uplift_pence=round(total_agile_pence - total_flat_pence, 4),
        snapshot_count=len(snapshots),
    )


def _find_rate(timestamp: datetime, slots: list[TariffSlot]) -> float | None:
    """Find the tariff rate that covers the given timestamp."""
    for slot in slots:
        if slot.interval_start <= timestamp < slot.interval_end:
            return slot.rate_inc_vat_pence
    return None
