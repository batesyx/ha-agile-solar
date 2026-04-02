"""Overnight charge target calculator: solar-aware overnight charging.

Analyses next-day export rates during peak solar hours (11:00-16:00)
to determine whether overnight charging should be reduced, leaving
headroom for free solar charging during low-rate periods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from octopus_export_optimizer.models.tariff import TariffSlot

# Fixed constants
_RATE_BUFFER_MULTIPLIER = 1.20  # 20% buffer above night import rate
_TRIGGER_RATIO = 0.70  # >70% of solar slots must be below threshold


@dataclass(frozen=True)
class OvernightChargeTarget:
    """Result of overnight charge target calculation."""

    target_soc_pct: float  # 0.0-1.0, quantized to 5% steps
    solar_opportunity_slots: int
    headroom_kwh: float
    estimated_savings_pence: float
    seasonal_max_pct: float  # The seasonal baseline used (0.80 or 0.95)


def calculate_overnight_charge_target(
    solar_hour_rates: list[TariffSlot],
    night_import_rate_pence: float,
    battery_capacity_kwh: float,
    minimum_overnight_soc_pct: float,
    seasonal_max_soc_pct: float,
    solar_charge_kwh_per_slot: float,
) -> OvernightChargeTarget | None:
    """Calculate the optimal overnight charge target.

    Args:
        solar_hour_rates: Export tariff slots during peak solar hours
            (11:00-16:00). Caller is responsible for filtering to this window.
        night_import_rate_pence: The overnight import rate (e.g. 7.5p).
        battery_capacity_kwh: Total usable battery capacity.
        minimum_overnight_soc_pct: Absolute floor for overnight charging (0.0-1.0).
        seasonal_max_soc_pct: Seasonal baseline maximum (0.80 Mar-Sep, 0.95 Oct-Feb).
        solar_charge_kwh_per_slot: Conservative kWh absorbed per 30-min slot.

    Returns:
        OvernightChargeTarget if rates are available, None otherwise.
    """
    if not solar_hour_rates:
        return None

    threshold = night_import_rate_pence * _RATE_BUFFER_MULTIPLIER

    # Count slots where export rate is below threshold
    solar_opportunity_slots = sum(
        1 for s in solar_hour_rates if s.rate_inc_vat_pence < threshold
    )
    total_slots = len(solar_hour_rates)

    # Check trigger ratio — need >70% of slots below threshold
    if solar_opportunity_slots / total_slots <= _TRIGGER_RATIO:
        return OvernightChargeTarget(
            target_soc_pct=seasonal_max_soc_pct,
            solar_opportunity_slots=solar_opportunity_slots,
            headroom_kwh=0.0,
            estimated_savings_pence=0.0,
            seasonal_max_pct=seasonal_max_soc_pct,
        )

    # Calculate headroom to leave for solar
    max_headroom_kwh = battery_capacity_kwh * (
        seasonal_max_soc_pct - minimum_overnight_soc_pct
    )
    headroom_kwh = min(
        solar_opportunity_slots * solar_charge_kwh_per_slot,
        max_headroom_kwh,
    )

    # Calculate target SoC
    raw_target = seasonal_max_soc_pct - headroom_kwh / battery_capacity_kwh
    target = max(minimum_overnight_soc_pct, raw_target)

    # Quantize to 5% steps for stability
    target = round(target * 20) / 20

    # Ensure we don't exceed bounds after quantization
    target = max(minimum_overnight_soc_pct, min(seasonal_max_soc_pct, target))

    # Estimated savings = headroom we created × night import rate
    actual_headroom = (seasonal_max_soc_pct - target) * battery_capacity_kwh
    estimated_savings = actual_headroom * night_import_rate_pence

    return OvernightChargeTarget(
        target_soc_pct=target,
        solar_opportunity_slots=solar_opportunity_slots,
        headroom_kwh=round(actual_headroom, 2),
        estimated_savings_pence=round(estimated_savings, 1),
        seasonal_max_pct=seasonal_max_soc_pct,
    )


def calculate_overnight_charge_power(
    now: datetime,
    current_soc_pct: float,
    target_soc_pct: float,
    battery_capacity_kwh: float,
    cheap_rate_start_hour: float,
    cheap_rate_end_hour: float,
    buffer_minutes: int = 30,
    min_power_kw: float = 0.5,
    max_power_kw: float = 5.0,
) -> float:
    """Calculate charge power based on remaining time in the cheap-rate window.

    Divides energy still needed by hours remaining (not the full window),
    so power stays stable as the battery charges and increases if charging
    falls behind schedule.

    Args:
        now: Current UTC time.
        current_soc_pct: Current battery SoC as fraction (0.0-1.0).
        target_soc_pct: Target SoC as fraction (0.0-1.0).
        battery_capacity_kwh: Total usable battery capacity.
        cheap_rate_start_hour: Start of cheap rate window as decimal hour (e.g. 23.5 = 23:30).
        cheap_rate_end_hour: End of cheap rate window as decimal hour (e.g. 5.5 = 05:30).
        buffer_minutes: Finish this many minutes before the window ends.
        min_power_kw: Minimum charge power floor.
        max_power_kw: Maximum charge power cap.

    Returns:
        Charge power in kW, clamped to [min_power_kw, max_power_kw].
    """
    if current_soc_pct >= target_soc_pct:
        return min_power_kw

    # Build the effective end datetime in local UK time, then convert to UTC
    uk_tz = ZoneInfo("Europe/London")
    local_now = now.astimezone(uk_tz)
    end_hour = int(cheap_rate_end_hour)
    end_minute = int((cheap_rate_end_hour % 1) * 60)
    effective_end = local_now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    effective_end -= timedelta(minutes=buffer_minutes)

    # If end time appears to be in the past, it's tomorrow
    if effective_end <= local_now:
        effective_end += timedelta(days=1)

    remaining_hours = (effective_end - local_now).total_seconds() / 3600.0

    if remaining_hours < 0.25:
        return max_power_kw

    energy_needed_kwh = (target_soc_pct - current_soc_pct) * battery_capacity_kwh
    charge_kw = energy_needed_kwh / remaining_hours

    return max(min_power_kw, min(max_power_kw, round(charge_kw, 2)))
