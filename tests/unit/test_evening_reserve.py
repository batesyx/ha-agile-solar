"""Tests for evening reserve SoC calculation."""

from datetime import datetime, timezone

import pytest

from octopus_export_optimizer.control.evening_reserve import (
    calculate_reserve_soc,
    sunset_hour_utc,
)


class TestSunsetHourUtc:
    def test_midsummer_late_sunset(self):
        """Summer solstice (~day 172) should give late sunset ~21.5 UTC."""
        dt = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
        sunset = sunset_hour_utc(dt)
        assert 20.5 < sunset < 22.0

    def test_midwinter_early_sunset(self):
        """Winter solstice (~day 355) should give early sunset ~16.0 UTC."""
        dt = datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc)
        sunset = sunset_hour_utc(dt)
        assert 15.5 < sunset < 17.0

    def test_spring_equinox_moderate(self):
        """Spring equinox (~day 80) should give sunset ~18.75 UTC."""
        dt = datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc)
        sunset = sunset_hour_utc(dt)
        assert 18.0 < sunset < 20.0


class TestCalculateReserveSoc:
    def test_past_cheap_rate_start_returns_floor(self):
        """Already in cheap window — no reserve needed."""
        now = datetime(2026, 3, 10, 23, 45, tzinfo=timezone.utc)
        result = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 11.52)
        assert result == 0.10

    def test_zero_battery_capacity_returns_floor(self):
        """Zero capacity battery — can't calculate, return floor."""
        now = datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)
        result = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 0.0)
        assert result == 0.10

    def test_before_sunset_uses_sunset_as_drain_start(self):
        """Before sunset — drain starts at sunset, not now."""
        # March 10 sunset is ~18.75 UTC
        now = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)
        result = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 11.52)
        # hours_on_battery = 23.5 - ~18.75 = ~4.75
        # kwh_needed = 4.75 * 1.2 = ~5.7
        # soc_fraction = 5.7 / 11.52 = ~0.495
        assert 0.40 < result < 0.60

    def test_after_sunset_uses_current_time(self):
        """After sunset — drain starts now."""
        now = datetime(2026, 3, 10, 20, 0, tzinfo=timezone.utc)
        result = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 11.52)
        # hours_on_battery = 23.5 - 20.0 = 3.5
        # kwh_needed = 3.5 * 1.2 = 4.2
        # soc_fraction = 4.2 / 11.52 = 0.365
        assert 0.30 < result < 0.45

    def test_large_load_clamped_to_ceiling(self):
        """Huge load on tiny battery — clamped to 0.90."""
        now = datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)
        result = calculate_reserve_soc(now, 23.5, 5.0, 5.0, 5.0)
        assert result == 0.90

    def test_extra_buffer_increases_reserve(self):
        """Extra buffer should increase the reserve."""
        now = datetime(2026, 3, 10, 20, 0, tzinfo=timezone.utc)
        without_buffer = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 11.52)
        with_buffer = calculate_reserve_soc(now, 23.5, 1.2, 3.0, 11.52)
        assert with_buffer > without_buffer

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime should still work (gets UTC attached)."""
        now = datetime(2026, 3, 10, 20, 0)  # No tzinfo
        result = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 11.52)
        assert 0.10 <= result <= 0.90

    def test_negative_hours_on_battery_returns_floor(self):
        """If sunset is after cheap rate start (shouldn't happen), return floor."""
        # Force this by using a summer time where sunset > 23.5 — won't happen,
        # but we can test with current_hour just before cheap_rate_start
        now = datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc)
        result = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 11.52)
        # hours_on_battery = 23.5 - 23.0 = 0.5
        # kwh_needed = 0.5 * 1.2 = 0.6
        # soc_fraction = 0.6 / 11.52 = 0.052 → clamped to 0.10
        assert result == 0.10

    def test_safety_margin_increases_reserve(self):
        """Safety margin multiplier should increase the reserve."""
        now = datetime(2026, 3, 10, 20, 0, tzinfo=timezone.utc)
        without_margin = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 11.52)
        with_margin = calculate_reserve_soc(
            now, 23.5, 1.2, 0.0, 11.52, safety_margin=1.5
        )
        assert with_margin > without_margin
        # 1.5x should increase by exactly 50%
        assert abs(with_margin / without_margin - 1.5) < 0.01

    def test_safety_margin_default_is_one(self):
        """Default safety_margin=1.0 should not change the result."""
        now = datetime(2026, 3, 10, 20, 0, tzinfo=timezone.utc)
        default_result = calculate_reserve_soc(now, 23.5, 1.2, 0.0, 11.52)
        explicit_result = calculate_reserve_soc(
            now, 23.5, 1.2, 0.0, 11.52, safety_margin=1.0
        )
        assert default_result == explicit_result

    def test_safety_margin_with_low_load_breaks_floor(self):
        """Low load that would hit 20% floor should break out with margin."""
        now = datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc)
        # 0.3 kW load, ~4.75 hours → 1.425 kWh / 11.52 = 12.4% → clamped to 10%
        without = calculate_reserve_soc(now, 23.5, 0.3, 0.0, 11.52)
        assert without < 0.20  # Below the typical 20% floor
        # With 2x margin → 2.85 kWh / 11.52 = 24.7%
        with_margin = calculate_reserve_soc(
            now, 23.5, 0.3, 0.0, 11.52, safety_margin=2.0
        )
        assert with_margin > 0.20
