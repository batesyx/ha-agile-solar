"""Tests for overnight charge target calculator."""

from datetime import datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.calculations.overnight_target import (
    OvernightChargeTarget,
    calculate_overnight_charge_power,
    calculate_overnight_charge_target,
)
from tests.factories import make_tariff_slot


def _make_solar_slots(
    rates: list[float],
    base_hour: int = 11,
) -> list:
    """Create tariff slots for solar hours starting at base_hour."""
    slots = []
    for i, rate in enumerate(rates):
        start = datetime(
            2026, 3, 10, base_hour, 0, tzinfo=timezone.utc
        ) + timedelta(minutes=30 * i)
        slots.append(make_tariff_slot(interval_start=start, rate_pence=rate))
    return slots


# Default test params
_DEFAULTS = dict(
    night_import_rate_pence=7.5,
    battery_capacity_kwh=11.52,
    minimum_overnight_soc_pct=0.40,
    seasonal_max_soc_pct=0.80,
    solar_charge_kwh_per_slot=1.25,
)


class TestNoRates:
    def test_no_rates_returns_none(self):
        result = calculate_overnight_charge_target(
            solar_hour_rates=[], **_DEFAULTS
        )
        assert result is None


class TestTriggerRatio:
    def test_all_high_rates_returns_seasonal_max(self):
        # All 10 slots above threshold (9p) — no opportunity
        slots = _make_solar_slots([15.0] * 10)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        assert result.target_soc_pct == 0.80
        assert result.solar_opportunity_slots == 0
        assert result.headroom_kwh == 0.0

    def test_below_70pct_threshold_returns_seasonal_max(self):
        # 6 of 10 slots low (60%) — below 70% trigger
        rates = [5.0] * 6 + [15.0] * 4
        slots = _make_solar_slots(rates)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        assert result.target_soc_pct == 0.80
        assert result.solar_opportunity_slots == 6

    def test_exactly_70pct_returns_seasonal_max(self):
        # 7 of 10 slots low (70%) — NOT above 70%, should not trigger
        rates = [5.0] * 7 + [15.0] * 3
        slots = _make_solar_slots(rates)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        assert result.target_soc_pct == 0.80

    def test_above_70pct_threshold_reduces_target(self):
        # 8 of 10 slots low (80%) — triggers reduction
        rates = [5.0] * 8 + [15.0] * 2
        slots = _make_solar_slots(rates)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        assert result.target_soc_pct < 0.80
        assert result.solar_opportunity_slots == 8


class TestTargetCalculation:
    def test_all_low_rates_returns_minimum(self):
        # All 10 slots below threshold — maximum headroom
        # 10 × 1.25 = 12.5 kWh, but capped at capacity × (0.80 - 0.40) = 4.608
        # target = 0.80 - 4.608/11.52 = 0.40
        slots = _make_solar_slots([5.0] * 10)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        assert result.target_soc_pct == 0.40

    def test_quantized_to_5_pct_steps(self):
        # 8 slots × 1.25 = 10 kWh headroom, capped at 4.608
        # raw target = 0.80 - 4.608/11.52 = 0.40 → quantizes to 0.40
        # Try with fewer slots: 8 of 10 low
        rates = [5.0] * 8 + [15.0] * 2
        slots = _make_solar_slots(rates)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        # Check it's on a 5% boundary
        assert (result.target_soc_pct * 20) == round(result.target_soc_pct * 20)

    def test_winter_seasonal_max(self):
        # Winter: seasonal_max = 0.95
        slots = _make_solar_slots([5.0] * 10)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots,
            night_import_rate_pence=7.5,
            battery_capacity_kwh=11.52,
            minimum_overnight_soc_pct=0.40,
            seasonal_max_soc_pct=0.95,
            solar_charge_kwh_per_slot=1.25,
        )
        assert result is not None
        assert result.target_soc_pct < 0.95
        assert result.target_soc_pct >= 0.40
        assert result.seasonal_max_pct == 0.95


class TestSavingsCalculation:
    def test_savings_calculation(self):
        # All 10 slots low, but headroom capped
        slots = _make_solar_slots([5.0] * 10)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        # savings = headroom_kwh × 7.5p
        expected_savings = result.headroom_kwh * 7.5
        assert abs(result.estimated_savings_pence - expected_savings) < 0.1


class TestThresholdDerivedFromNightRate:
    def test_threshold_is_night_rate_times_1_20(self):
        # Night rate = 7.5p → threshold = 9.0p
        # Slots at 8.9p should be below threshold (opportunity)
        # Slots at 9.1p should be above threshold (not opportunity)
        rates_just_below = [8.9] * 10
        slots = _make_solar_slots(rates_just_below)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        assert result.solar_opportunity_slots == 10
        assert result.target_soc_pct < 0.80

    def test_rates_at_threshold_not_counted(self):
        # Slots exactly at 9.0p — not strictly less than threshold
        rates_at_threshold = [9.0] * 10
        slots = _make_solar_slots(rates_at_threshold)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        assert result.solar_opportunity_slots == 0
        assert result.target_soc_pct == 0.80

    def test_different_night_rate(self):
        # Night rate = 10p → threshold = 12p
        # Slots at 11p should count as opportunity
        slots = _make_solar_slots([11.0] * 10)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots,
            night_import_rate_pence=10.0,
            battery_capacity_kwh=11.52,
            minimum_overnight_soc_pct=0.40,
            seasonal_max_soc_pct=0.80,
            solar_charge_kwh_per_slot=1.25,
        )
        assert result is not None
        assert result.solar_opportunity_slots == 10
        assert result.target_soc_pct < 0.80


class TestHeadroomCapping:
    def test_headroom_capped_at_capacity_range(self):
        # With minimum=0.40, seasonal_max=0.80, capacity=11.52
        # Max headroom = 11.52 × 0.40 = 4.608 kWh
        # 10 slots × 1.25 = 12.5 kWh > 4.608 → capped
        slots = _make_solar_slots([5.0] * 10)
        result = calculate_overnight_charge_target(
            solar_hour_rates=slots, **_DEFAULTS
        )
        assert result is not None
        assert result.target_soc_pct >= 0.40
        # headroom should be capacity × (seasonal_max - target)
        max_possible = 11.52 * (0.80 - 0.40)
        assert result.headroom_kwh <= max_possible + 0.01


class TestCalculateOvernightChargePower:
    """Tests for static trickle charge power calculation."""

    # Default window: 23:30 to 05:30 = 6 hours, minus 30 min buffer = 5.5 hours
    _WINDOW = dict(cheap_rate_start_hour=23.5, cheap_rate_end_hour=5.5)

    def test_basic_calculation(self):
        """10% → 80% over 5.5 hours = ~1.47 kW."""
        power = calculate_overnight_charge_power(
            current_soc_pct=0.10,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
        )
        # 8.064 kWh / 5.5h ≈ 1.47 kW
        assert 1.3 < power < 1.7

    def test_full_charge_10_to_95(self):
        """10% → 95% over 5.5 hours = ~1.78 kW."""
        power = calculate_overnight_charge_power(
            current_soc_pct=0.10,
            target_soc_pct=0.95,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
        )
        # 9.792 kWh / 5.5h ≈ 1.78 kW
        assert 1.6 < power < 2.0

    def test_already_at_target_returns_min(self):
        power = calculate_overnight_charge_power(
            current_soc_pct=0.80,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
        )
        assert power == 0.5

    def test_above_target_returns_min(self):
        power = calculate_overnight_charge_power(
            current_soc_pct=0.90,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
        )
        assert power == 0.5

    def test_clamped_to_min(self):
        """Small energy needed → floor at min_power_kw."""
        power = calculate_overnight_charge_power(
            current_soc_pct=0.78,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
        )
        assert power == 0.5

    def test_clamped_to_max(self):
        """Huge energy in tiny window → cap at max_power_kw."""
        power = calculate_overnight_charge_power(
            current_soc_pct=0.10,
            target_soc_pct=0.95,
            battery_capacity_kwh=11.52,
            cheap_rate_start_hour=5.0,
            cheap_rate_end_hour=5.5,
            buffer_minutes=25,
        )
        assert power == 5.0

    def test_static_regardless_of_time_in_window(self):
        """Same SoC and target always produces the same power."""
        kwargs = dict(
            current_soc_pct=0.30,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
        )
        power = calculate_overnight_charge_power(**kwargs)
        # Call again — same result (no time dependency)
        power2 = calculate_overnight_charge_power(**kwargs)
        assert power == power2

    def test_lower_soc_needs_higher_power(self):
        """Lower starting SoC → more energy → higher power."""
        power_low = calculate_overnight_charge_power(
            current_soc_pct=0.30,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
        )
        power_high = calculate_overnight_charge_power(
            current_soc_pct=0.60,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
        )
        assert power_low > power_high

    def test_midnight_crossing_window(self):
        """23:30 to 05:30 crosses midnight = 6 hours total."""
        power = calculate_overnight_charge_power(
            current_soc_pct=0.10,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            cheap_rate_start_hour=23.5,
            cheap_rate_end_hour=5.5,
        )
        # 8.064 kWh / 5.5h (6h - 30min buffer)
        assert 1.3 < power < 1.7

    def test_non_crossing_window(self):
        """Window within same day (e.g. 00:00 to 06:00)."""
        power = calculate_overnight_charge_power(
            current_soc_pct=0.10,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            cheap_rate_start_hour=0.0,
            cheap_rate_end_hour=6.0,
        )
        # 8.064 kWh / 5.5h (6h - 30min buffer)
        assert 1.3 < power < 1.7

    @pytest.mark.parametrize("buffer", [0, 15, 30, 60])
    def test_buffer_affects_power(self, buffer):
        """Larger buffer → shorter effective window → higher power."""
        power = calculate_overnight_charge_power(
            current_soc_pct=0.10,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
            buffer_minutes=buffer,
        )
        assert power > 0

    def test_custom_min_max(self):
        power = calculate_overnight_charge_power(
            current_soc_pct=0.79,
            target_soc_pct=0.80,
            battery_capacity_kwh=11.52,
            **self._WINDOW,
            min_power_kw=1.0,
            max_power_kw=3.0,
        )
        assert power == 1.0  # Tiny energy → clamped to custom min
