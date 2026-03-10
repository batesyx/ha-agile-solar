"""Overnight charge target calculator: solar-aware overnight charging.

Analyses next-day export rates during peak solar hours (11:00-16:00)
to determine whether overnight charging should be reduced, leaving
headroom for free solar charging during low-rate periods.
"""

from __future__ import annotations

from dataclasses import dataclass

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
