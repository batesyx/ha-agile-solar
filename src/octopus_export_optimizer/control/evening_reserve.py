"""Dynamic evening SoC reserve calculation.

Calculates the minimum battery SoC needed to power the house
from now until the cheap import rate starts (typically 23:30),
accounting for sunset time and historical evening consumption.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def sunset_hour_utc(dt: datetime) -> float:
    """Approximate sunset hour (UTC) for UK latitude on the given date.

    Uses the same sinusoidal model as solar_profile.py.
    """
    day_of_year = dt.timetuple().tm_yday
    return 18.75 + 2.75 * math.sin(2 * math.pi * (day_of_year - 80) / 365)


def calculate_reserve_soc(
    now: datetime,
    cheap_rate_start_hour: float,
    avg_load_kw: float,
    extra_buffer_kwh: float,
    battery_capacity_kwh: float,
) -> float:
    """Calculate the minimum SoC fraction to reserve for evening self-consumption.

    Args:
        now: Current UTC datetime.
        cheap_rate_start_hour: Hour (UTC) when cheap import rate begins (e.g. 23.5 = 23:30).
        avg_load_kw: Rolling average evening household load in kW.
        extra_buffer_kwh: User-adjustable extra buffer in kWh.
        battery_capacity_kwh: Total battery capacity.

    Returns:
        Reserve SoC as a fraction (0.0 to 0.90). Clamped to this range.
    """
    if battery_capacity_kwh <= 0:
        return 0.10

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    current_hour = now.hour + now.minute / 60.0
    sunset = sunset_hour_utc(now)

    # If it's already past the cheap rate start, or before sunset, calculate differently
    if current_hour >= cheap_rate_start_hour:
        # Already in cheap rate window — no reserve needed
        return 0.10

    # Hours of battery-only consumption = from max(now, sunset) to cheap_rate_start
    earliest_battery_drain = max(current_hour, sunset)
    hours_on_battery = cheap_rate_start_hour - earliest_battery_drain

    if hours_on_battery <= 0:
        return 0.10

    kwh_needed = hours_on_battery * avg_load_kw + extra_buffer_kwh
    soc_fraction = kwh_needed / battery_capacity_kwh

    return max(0.10, min(0.90, soc_fraction))


def get_rolling_avg_evening_load(
    ha_state_repo: object,
    now: datetime,
    days: int = 7,
    default_kw: float = 1.2,
) -> float:
    """Calculate rolling average evening load from stored HA state snapshots.

    Looks at load_power values recorded between sunset and 23:30
    over the last `days` days.

    Args:
        ha_state_repo: HaStateRepo instance with get_by_range() method.
        now: Current UTC datetime.
        days: Number of days to look back.
        default_kw: Fallback value if no data available.

    Returns:
        Average load in kW.
    """
    from datetime import timedelta

    sunset = sunset_hour_utc(now)
    sunset_minutes = int(sunset * 60)
    cheap_minutes = 23 * 60 + 30  # 23:30

    total_load = 0.0
    count = 0

    for day_offset in range(1, days + 1):
        day = now - timedelta(days=day_offset)
        snapshots = ha_state_repo.get_by_range(
            day.replace(hour=int(sunset), minute=sunset_minutes % 60, second=0),
            day.replace(hour=23, minute=30, second=0),
        )
        for snap in snapshots:
            if snap.load_power_kw is not None and snap.load_power_kw > 0:
                total_load += snap.load_power_kw
                count += 1

    if count == 0:
        logger.debug(
            "No evening load data for last %d days, using fallback %.1f kW",
            days,
            default_kw,
        )
        return default_kw

    return total_load / count
