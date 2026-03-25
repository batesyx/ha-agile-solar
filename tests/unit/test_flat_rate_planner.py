"""Tests for the flat-rate export planner."""

from datetime import datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.calculations.flat_rate_planner import (
    DischargeWindow,
    build_flat_rate_plan,
    _local_hour_to_utc,
)
from tests.factories import make_tariff_slot

# 18:00 UTC on 2026-03-10 (GMT, no BST offset)
NOW = datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)

# 18:00 UTC on 2026-07-10 (BST: UTC+1, so local time is 19:00)
NOW_BST = datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc)

# 04:00 UTC — before morning window
NOW_EARLY = datetime(2026, 3, 10, 4, 0, tzinfo=timezone.utc)


def _slot(start: datetime, rate: float = 12.0):
    """Create a tariff slot at an absolute start time."""
    return make_tariff_slot(interval_start=start, rate_pence=rate)


def _evening_window(kwh: float = 5.0):
    return DischargeWindow(start_hour=21.0, end_hour=23.5, exportable_kwh=kwh)


def _morning_window(kwh: float = 2.3):
    return DischargeWindow(start_hour=5.5, end_hour=9.0, exportable_kwh=kwh)


def _evening_slots():
    """5 slots: 21:00, 21:30, 22:00, 22:30, 23:00 UTC."""
    base = datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc)
    return [_slot(base + timedelta(minutes=30 * i)) for i in range(5)]


def _morning_slots():
    """7 slots: 05:30, 06:00, 06:30, 07:00, 07:30, 08:00, 08:30 UTC."""
    base = datetime(2026, 3, 10, 5, 30, tzinfo=timezone.utc)
    return [_slot(base + timedelta(minutes=30 * i)) for i in range(7)]


def _plan(
    windows=None,
    slots=None,
    max_kw: float = 5.0,
    now: datetime = NOW,
):
    """Build a flat-rate plan with sensible defaults."""
    if windows is None:
        windows = [_evening_window()]
    if slots is None:
        slots = _evening_slots()
    return build_flat_rate_plan(
        now=now,
        upcoming_slots=slots,
        max_discharge_kw=max_kw,
        battery_capacity_kwh=11.52,
        round_trip_efficiency=0.90,
        windows=windows,
    )


class TestSingleWindow:
    def test_spreads_evenly_across_window_slots(self):
        """5 slots in evening window — energy spread evenly across all 5."""
        plan = _plan()
        assert plan is not None
        assert len(plan.planned_slots) == 5
        powers = {s.discharge_kw for s in plan.planned_slots}
        assert len(powers) == 1

    def test_discharge_power_is_minimum_needed(self):
        plan = _plan()
        assert plan is not None
        # 5.0 * sqrt(0.9) ≈ 4.7434 / 5 slots / 0.5h ≈ 1.897 kW
        assert plan.discharge_kw == pytest.approx(1.897, abs=0.01)

    def test_zero_exportable_returns_none(self):
        result = _plan(windows=[_evening_window(0.0)])
        assert result is None

    def test_empty_slots_returns_none(self):
        result = _plan(slots=[])
        assert result is None

    def test_past_end_hour_returns_none(self):
        late = datetime(2026, 3, 10, 23, 45, tzinfo=timezone.utc)
        result = _plan(now=late)
        assert result is None

    def test_excludes_slots_before_window(self):
        early = _slot(datetime(2026, 3, 10, 20, 0, tzinfo=timezone.utc))
        inside = _slot(datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc))
        plan = _plan(slots=[early, inside])
        assert plan is not None
        assert len(plan.planned_slots) == 1
        assert plan.planned_slots[0].interval_start.hour == 21

    def test_excludes_slots_after_window(self):
        inside = _slot(datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc))
        outside = _slot(datetime(2026, 3, 10, 23, 30, tzinfo=timezone.utc))
        plan = _plan(slots=[inside, outside])
        assert plan is not None
        assert len(plan.planned_slots) == 1

    def test_caps_at_max_discharge_kw(self):
        slots = [_slot(datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc))]
        plan = _plan(windows=[_evening_window(10.0)], slots=slots, max_kw=5.0)
        assert plan is not None
        assert plan.discharge_kw == 5.0

    def test_recalculation_fewer_slots_higher_power(self):
        slots = _evening_slots()
        plan_full = _plan(windows=[_evening_window(5.0)], slots=slots)
        assert plan_full is not None
        assert len(plan_full.planned_slots) == 5

        later = datetime(2026, 3, 10, 22, 1, tzinfo=timezone.utc)
        plan_later = _plan(windows=[_evening_window(5.0)], slots=slots, now=later)
        assert plan_later is not None
        assert len(plan_later.planned_slots) == 3
        assert plan_later.discharge_kw > plan_full.discharge_kw

    def test_drops_tiny_slots(self):
        plan = _plan(windows=[_evening_window(0.05)])
        assert plan is None

    def test_slots_sorted_by_time(self):
        slots = [
            _slot(datetime(2026, 3, 10, 23, 0, tzinfo=timezone.utc)),
            _slot(datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc)),
            _slot(datetime(2026, 3, 10, 22, 0, tzinfo=timezone.utc)),
        ]
        plan = _plan(slots=slots)
        assert plan is not None
        starts = [s.interval_start for s in plan.planned_slots]
        assert starts == sorted(starts)

    def test_no_rate_filtering(self):
        slots = [
            _slot(datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc), rate=1.0),
            _slot(datetime(2026, 3, 10, 21, 30, tzinfo=timezone.utc), rate=50.0),
        ]
        plan = _plan(slots=slots)
        assert plan is not None
        assert len(plan.planned_slots) == 2


class TestMultipleWindows:
    def test_morning_and_evening_combined(self):
        """Both windows produce slots in a single plan."""
        all_slots = _morning_slots() + _evening_slots()
        plan = _plan(
            windows=[_morning_window(2.3), _evening_window(5.0)],
            slots=all_slots,
            now=NOW_EARLY,
        )
        assert plan is not None
        assert len(plan.planned_slots) == 12  # 7 morning + 5 evening

    def test_different_discharge_power_per_window(self):
        """Morning and evening windows have different energy, so different kW."""
        all_slots = _morning_slots() + _evening_slots()
        plan = _plan(
            windows=[_morning_window(2.3), _evening_window(5.0)],
            slots=all_slots,
            now=NOW_EARLY,
        )
        assert plan is not None
        morning = [s for s in plan.planned_slots if s.interval_start.hour < 12]
        evening = [s for s in plan.planned_slots if s.interval_start.hour >= 21]
        assert len(morning) == 7
        assert len(evening) == 5
        # Different energy budgets → different discharge powers
        assert morning[0].discharge_kw != evening[0].discharge_kw

    def test_skip_empty_window(self):
        """Window with 0 energy is skipped, other still works."""
        plan = _plan(
            windows=[_morning_window(0.0), _evening_window(5.0)],
            slots=_evening_slots(),
        )
        assert plan is not None
        assert len(plan.planned_slots) == 5

    def test_skip_past_window(self):
        """Morning window is past but evening window still active."""
        plan = _plan(
            windows=[_morning_window(2.3), _evening_window(5.0)],
            slots=_morning_slots() + _evening_slots(),
            now=NOW,  # 18:00 — past morning window
        )
        assert plan is not None
        # Only evening slots
        assert all(s.interval_start.hour >= 21 for s in plan.planned_slots)

    def test_all_windows_past_returns_none(self):
        late = datetime(2026, 3, 10, 23, 45, tzinfo=timezone.utc)
        result = _plan(
            windows=[_morning_window(), _evening_window()],
            slots=_morning_slots() + _evening_slots(),
            now=late,
        )
        assert result is None


class TestBSTHandling:
    def test_local_hour_in_gmt(self):
        utc_dt = _local_hour_to_utc(NOW, 21.0)
        assert utc_dt.hour == 21
        assert utc_dt.tzinfo == timezone.utc

    def test_local_hour_in_bst(self):
        utc_dt = _local_hour_to_utc(NOW_BST, 21.0)
        assert utc_dt.hour == 20
        assert utc_dt.tzinfo == timezone.utc

    def test_bst_window_shifts_utc(self):
        inside = _slot(datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc))
        outside = _slot(datetime(2026, 7, 10, 22, 30, tzinfo=timezone.utc))
        plan = _plan(slots=[inside, outside], now=NOW_BST)
        assert plan is not None
        assert len(plan.planned_slots) == 1
        assert plan.planned_slots[0].interval_start.hour == 20

    def test_half_hour_end(self):
        utc_dt = _local_hour_to_utc(NOW, 23.5)
        assert utc_dt.hour == 23
        assert utc_dt.minute == 30

    def test_morning_window_bst(self):
        """In BST, 05:30 local = 04:30 UTC."""
        utc_dt = _local_hour_to_utc(NOW_BST, 5.5)
        assert utc_dt.hour == 4
        assert utc_dt.minute == 30
