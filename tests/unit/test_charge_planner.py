"""Tests for the solar charge planner."""

from datetime import datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.calculations.charge_planner import (
    ChargePlan,
    ChargingSlot,
    build_charge_plan,
)
from octopus_export_optimizer.models.export_plan import ExportPlan, PlannedSlot
from tests.factories import make_tariff_slot


# Helper to create a batch of half-hour slots across a day
def _make_day_slots(
    base: datetime,
    rates: dict[int, float],
    default_rate: float = 5.0,
) -> list:
    """Create 48 half-hour slots for a day.

    rates: {hour: rate} overrides for specific hours.
    """
    slots = []
    for i in range(48):
        start = base + timedelta(minutes=30 * i)
        hour = start.hour
        rate = rates.get(hour, default_rate)
        slots.append(make_tariff_slot(interval_start=start, rate_pence=rate))
    return slots


def _make_export_plan(
    base: datetime,
    slot_hours: list[int],
    rate: float = 18.0,
    discharge_kw: float = 5.0,
) -> ExportPlan:
    """Create an ExportPlan with discharge slots at given hours."""
    planned = []
    for h in slot_hours:
        start = base.replace(hour=h, minute=0)
        planned.append(PlannedSlot(
            interval_start=start,
            interval_end=start + timedelta(minutes=30),
            rate_pence=rate,
            discharge_kw=discharge_kw,
            expected_kwh=discharge_kw * 0.5,
        ))
    return ExportPlan(
        created_at=base,
        planned_slots=planned,
        total_planned_kwh=discharge_kw * 0.5 * len(planned),
        exportable_kwh=5.0,
        discharge_kw=discharge_kw,
    )


class TestBuildChargePlan:
    BASE = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)  # 08:00 UTC

    def test_no_headroom_returns_none(self):
        slots = _make_day_slots(self.BASE.replace(hour=0), {16: 20.0})
        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=None,
            battery_headroom_kwh=0.05,  # below 0.1 threshold
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )
        assert result is None

    def test_no_upcoming_slots_returns_none(self):
        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=[],
            export_plan=None,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )
        assert result is None

    def test_no_slots_above_threshold_returns_none(self):
        """All rates below threshold, no plan — nothing to store for."""
        slots = _make_day_slots(self.BASE.replace(hour=0), {})  # all 5p
        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=None,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )
        assert result is None

    def test_basic_charge_plan_with_export_plan(self):
        """Morning low rates + afternoon high-rate discharge plan → charging slots."""
        base_day = self.BASE.replace(hour=0)
        slots = _make_day_slots(base_day, {
            8: 5.0, 9: 6.0, 10: 7.0, 11: 8.0,  # low morning rates
            16: 20.0, 17: 18.0,  # high afternoon (discharge)
        })
        plan = _make_export_plan(base_day, [16, 17], rate=19.0)

        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        assert len(result.charging_slots) > 0
        # All charging slots should be before 16:00 (first discharge)
        for slot in result.charging_slots:
            assert slot.interval_start.hour < 16
        # Breakeven should be target_rate * sqrt(0.90)
        expected_breakeven = 19.0 * (0.90 ** 0.5)
        assert abs(result.breakeven_rate_pence - expected_breakeven) < 0.1

    def test_breakeven_calculation(self):
        """Verify breakeven = target_rate × sqrt(efficiency)."""
        base_day = self.BASE.replace(hour=0)
        slots = _make_day_slots(base_day, {8: 5.0, 16: 25.0})
        plan = _make_export_plan(base_day, [16], rate=25.0)

        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        # 25.0 * sqrt(0.90) ≈ 23.72
        assert abs(result.breakeven_rate_pence - 25.0 * 0.90 ** 0.5) < 0.1

    def test_slots_above_breakeven_excluded(self):
        """Slots at or above breakeven should not be charging slots."""
        base_day = self.BASE.replace(hour=0)
        # breakeven ≈ 15.0 * 0.949 ≈ 14.2p
        slots = _make_day_slots(base_day, {
            8: 14.5,  # above breakeven — should NOT be a charging slot
            9: 5.0,   # below breakeven — should be
            16: 15.0,
        })
        plan = _make_export_plan(base_day, [16], rate=15.0)

        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        rates = {s.export_rate_pence for s in result.charging_slots}
        assert 14.5 not in rates
        assert 5.0 in rates

    def test_charging_stops_before_discharge(self):
        """Slots at or after first discharge slot are excluded."""
        base_day = self.BASE.replace(hour=0)
        slots = _make_day_slots(base_day, {
            8: 5.0, 9: 5.0,    # before discharge — eligible
            14: 5.0, 15: 5.0,  # before discharge — eligible
            16: 20.0,          # discharge slot
            17: 5.0,           # after discharge — excluded
        })
        plan = _make_export_plan(base_day, [16], rate=20.0)

        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        for slot in result.charging_slots:
            assert slot.interval_start.hour < 16

    def test_outside_solar_hours_excluded(self):
        """Slots before 06:00 or at/after 16:00 are excluded."""
        base_day = self.BASE.replace(hour=0)
        slots = _make_day_slots(base_day, {
            4: 2.0,   # before solar hours
            5: 2.0,   # before solar hours
            8: 5.0,   # solar hours
            17: 20.0, # discharge target
        })
        plan = _make_export_plan(base_day, [17], rate=20.0)

        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        for slot in result.charging_slots:
            assert 6 <= slot.interval_start.hour < 16

    def test_ended_slots_excluded(self):
        """Slots that have already ended are not included."""
        now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
        base_day = now.replace(hour=0)
        slots = _make_day_slots(base_day, {
            8: 5.0,   # ended (before 10:00)
            9: 5.0,   # ended
            10: 5.0,  # current — interval_end > now
            11: 5.0,  # future
            16: 20.0,
        })
        plan = _make_export_plan(base_day, [16], rate=20.0)

        result = build_charge_plan(
            now=now,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        for slot in result.charging_slots:
            assert slot.interval_end > now

    def test_slots_sorted_by_time(self):
        """Output charging slots are in chronological order."""
        base_day = self.BASE.replace(hour=0)
        slots = _make_day_slots(base_day, {
            8: 5.0, 9: 5.0, 10: 5.0, 16: 20.0,
        })
        plan = _make_export_plan(base_day, [16], rate=20.0)

        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        starts = [s.interval_start for s in result.charging_slots]
        assert starts == sorted(starts)

    def test_without_export_plan_uses_best_upcoming(self):
        """Without a discharge plan, target rate comes from best upcoming."""
        base_day = self.BASE.replace(hour=0)
        slots = _make_day_slots(base_day, {
            8: 5.0, 9: 6.0, 16: 22.0,  # 22p is best rate
        })

        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=None,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        assert result.target_discharge_rate_pence == 22.0

    def test_value_of_storage_calculated(self):
        """Each slot has correct value_of_storage = breakeven - export_rate."""
        base_day = self.BASE.replace(hour=0)
        slots = _make_day_slots(base_day, {8: 5.0, 16: 20.0})
        plan = _make_export_plan(base_day, [16], rate=20.0)

        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=5.0,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
        )

        assert result is not None
        for slot in result.charging_slots:
            expected = round(result.breakeven_rate_pence - slot.export_rate_pence, 2)
            assert slot.value_of_storage_pence == expected

    def test_cheapest_slots_selected_first(self):
        """Only the N cheapest slots needed to fill headroom are selected."""
        base_day = self.BASE.replace(hour=0)
        # Create specific slots with distinct rates (default 20p = above breakeven)
        slots = _make_day_slots(base_day, {16: 20.0}, default_rate=20.0)
        # Override 4 specific half-hour slots with low rates
        for s in slots:
            if s.interval_start.hour == 8 and s.interval_start.minute == 0:
                slots[slots.index(s)] = make_tariff_slot(interval_start=s.interval_start, rate_pence=3.0)
            elif s.interval_start.hour == 9 and s.interval_start.minute == 0:
                slots[slots.index(s)] = make_tariff_slot(interval_start=s.interval_start, rate_pence=7.0)
            elif s.interval_start.hour == 10 and s.interval_start.minute == 0:
                slots[slots.index(s)] = make_tariff_slot(interval_start=s.interval_start, rate_pence=11.0)
            elif s.interval_start.hour == 11 and s.interval_start.minute == 0:
                slots[slots.index(s)] = make_tariff_slot(interval_start=s.interval_start, rate_pence=14.0)
        plan = _make_export_plan(base_day, [16], rate=20.0)

        # 2.5 kWh headroom / 1.25 kWh per slot = 2 slots needed
        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=2.5,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
            solar_charge_kwh_per_slot=1.25,
        )

        assert result is not None
        assert len(result.charging_slots) == 2
        rates = {s.export_rate_pence for s in result.charging_slots}
        assert rates == {3.0, 7.0}  # two cheapest

    def test_small_headroom_selects_single_cheapest(self):
        """With minimal headroom, only the single cheapest slot is selected."""
        base_day = self.BASE.replace(hour=0)
        # Default 20p (above breakeven), override specific slots
        slots = _make_day_slots(base_day, {16: 20.0}, default_rate=20.0)
        for s in slots:
            if s.interval_start.hour == 8 and s.interval_start.minute == 0:
                slots[slots.index(s)] = make_tariff_slot(interval_start=s.interval_start, rate_pence=3.0)
            elif s.interval_start.hour == 9 and s.interval_start.minute == 0:
                slots[slots.index(s)] = make_tariff_slot(interval_start=s.interval_start, rate_pence=7.0)
            elif s.interval_start.hour == 10 and s.interval_start.minute == 0:
                slots[slots.index(s)] = make_tariff_slot(interval_start=s.interval_start, rate_pence=12.0)
        plan = _make_export_plan(base_day, [16], rate=20.0)

        # 0.5 kWh headroom → 1 slot needed
        result = build_charge_plan(
            now=self.BASE,
            upcoming_slots=slots,
            export_plan=plan,
            battery_headroom_kwh=0.5,
            round_trip_efficiency=0.90,
            export_threshold_pence=15.0,
            solar_charge_kwh_per_slot=1.25,
        )

        assert result is not None
        assert len(result.charging_slots) == 1
        assert result.charging_slots[0].export_rate_pence == 3.0


class TestChargePlanHelpers:
    def test_is_charging_now_true(self):
        now = datetime(2026, 6, 15, 9, 15, tzinfo=timezone.utc)
        plan = ChargePlan(
            charging_slots=[
                ChargingSlot(
                    interval_start=datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc),
                    interval_end=datetime(2026, 6, 15, 9, 30, tzinfo=timezone.utc),
                    export_rate_pence=5.0,
                    value_of_storage_pence=10.0,
                ),
            ],
            target_discharge_rate_pence=20.0,
            breakeven_rate_pence=18.0,
            headroom_kwh=5.0,
        )
        assert plan.is_charging_now(now) is True

    def test_is_charging_now_false(self):
        now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
        plan = ChargePlan(
            charging_slots=[
                ChargingSlot(
                    interval_start=datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc),
                    interval_end=datetime(2026, 6, 15, 9, 30, tzinfo=timezone.utc),
                    export_rate_pence=5.0,
                    value_of_storage_pence=10.0,
                ),
            ],
            target_discharge_rate_pence=20.0,
            breakeven_rate_pence=18.0,
            headroom_kwh=5.0,
        )
        assert plan.is_charging_now(now) is False

    def test_get_current_slot(self):
        now = datetime(2026, 6, 15, 9, 15, tzinfo=timezone.utc)
        slot = ChargingSlot(
            interval_start=datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc),
            interval_end=datetime(2026, 6, 15, 9, 30, tzinfo=timezone.utc),
            export_rate_pence=5.0,
            value_of_storage_pence=10.0,
        )
        plan = ChargePlan(
            charging_slots=[slot],
            target_discharge_rate_pence=20.0,
            breakeven_rate_pence=18.0,
            headroom_kwh=5.0,
        )
        assert plan.get_current_slot(now) == slot

    def test_get_current_slot_none(self):
        now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
        plan = ChargePlan(
            charging_slots=[
                ChargingSlot(
                    interval_start=datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc),
                    interval_end=datetime(2026, 6, 15, 9, 30, tzinfo=timezone.utc),
                    export_rate_pence=5.0,
                    value_of_storage_pence=10.0,
                ),
            ],
            target_discharge_rate_pence=20.0,
            breakeven_rate_pence=18.0,
            headroom_kwh=5.0,
        )
        assert plan.get_current_slot(now) is None
