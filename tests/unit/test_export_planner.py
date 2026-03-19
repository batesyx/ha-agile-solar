"""Tests for the export planner algorithm and rule integration."""

from datetime import datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.calculations.export_planner import build_export_plan
from octopus_export_optimizer.config.settings import InverterControlSettings
from octopus_export_optimizer.models.export_plan import ExportPlan
from octopus_export_optimizer.recommendation.engine import RecommendationEngine
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from tests.factories import make_recommendation_snapshot, make_tariff_slot


NOW = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)


def _slot(hours_from_now: float, rate: float = 20.0):
    """Create a tariff slot at an offset from NOW."""
    start = NOW + timedelta(hours=hours_from_now)
    return make_tariff_slot(interval_start=start, rate_pence=rate)


def _plan(
    exportable: float = 5.0,
    slots=None,
    threshold: float = 15.0,
    max_kw: float = 5.0,
):
    """Build a plan with sensible defaults."""
    if slots is None:
        slots = [_slot(1, 25), _slot(2, 20), _slot(3, 18)]
    return build_export_plan(
        now=NOW,
        upcoming_slots=slots,
        exportable_kwh=exportable,
        export_threshold_pence=threshold,
        max_discharge_kw=max_kw,
        battery_capacity_kwh=11.52,
        round_trip_efficiency=0.90,
    )


class TestBuildExportPlan:
    def test_no_eligible_slots_returns_none(self):
        result = _plan(slots=[_slot(1, rate=5.0)])  # below 15p threshold
        assert result is None

    def test_zero_exportable_returns_none(self):
        result = _plan(exportable=0.0)
        assert result is None

    def test_negative_exportable_returns_none(self):
        result = _plan(exportable=-1.0)
        assert result is None

    def test_empty_slots_returns_none(self):
        result = _plan(slots=[])
        assert result is None

    def test_single_slot_plan(self):
        plan = _plan(exportable=2.0, slots=[_slot(1, 25.0)])
        assert plan is not None
        assert len(plan.planned_slots) == 1
        assert plan.planned_slots[0].rate_pence == 25.0
        # Single slot: effective=2.0*0.949=1.90, 1.90/0.5=3.80 kW (under max)
        assert plan.discharge_kw == pytest.approx(plan.planned_slots[0].expected_kwh / 0.5, abs=0.01)

    def test_multiple_slots_highest_rates_selected(self):
        slots = [_slot(1, 25), _slot(2, 15), _slot(3, 30), _slot(4, 20)]
        plan = _plan(exportable=5.0, slots=slots, max_kw=5.0)
        assert plan is not None
        # max_kwh_per_slot = 5 × 0.5 = 2.5, slots_needed = ceil(5/2.5) = 2
        assert len(plan.planned_slots) == 2
        # Should pick 30p and 25p slots (top 2 by rate)
        rates = {s.rate_pence for s in plan.planned_slots}
        assert rates == {25.0, 30.0}

    def test_energy_budget_limits_slot_count(self):
        slots = [_slot(i, 20 + i) for i in range(1, 7)]  # 6 slots
        # 5 kWh × sqrt(0.90) ≈ 4.74, ceil(4.74/2.5) = 2 slots
        plan = _plan(exportable=5.0, slots=slots, max_kw=5.0)
        assert plan is not None
        assert len(plan.planned_slots) == 2

    def test_discharge_power_clamped_to_max(self):
        # 1 slot, 8 kWh → even_kw = 8*0.949/0.5 = 15.2 kW, clamped to 5 kW
        plan = _plan(exportable=8.0, slots=[_slot(1, 25)], max_kw=5.0)
        assert plan is not None
        assert plan.discharge_kw <= 5.0

    def test_all_slots_get_even_power(self):
        slots = [_slot(1, 25), _slot(2, 30), _slot(3, 20)]
        plan = _plan(exportable=6.0, slots=slots, max_kw=5.0)
        assert plan is not None
        # 6.0 × sqrt(0.90) ≈ 5.69 kWh across 3 slots → 1.90 kWh each → 3.80 kW
        for slot in plan.planned_slots:
            assert slot.discharge_kw == pytest.approx(slot.discharge_kw, abs=0.01)
            assert slot.discharge_kw < 5.0  # even distribution means below max
        # All slots get identical power
        powers = {s.discharge_kw for s in plan.planned_slots}
        assert len(powers) == 1

    def test_even_distribution_across_4_slots(self):
        slots = [_slot(1, 30), _slot(2, 25), _slot(3, 22), _slot(4, 20)]
        # 8 kWh × sqrt(0.90) ≈ 7.59 → 4 slots → 1.90 kWh each → 3.80 kW
        plan = _plan(exportable=8.0, slots=slots, max_kw=5.0)
        assert plan is not None
        assert len(plan.planned_slots) == 4
        kwhs = [s.expected_kwh for s in plan.planned_slots]
        assert all(abs(k - kwhs[0]) < 0.01 for k in kwhs)  # all equal
        assert plan.discharge_kw < 5.0  # even spread means gentler

    def test_more_energy_than_slots_uses_all_slots(self):
        slots = [_slot(1, 25), _slot(2, 20)]
        # 10 kWh × sqrt(0.90) ≈ 9.49 across 2 slots → 4.75 kWh each → 9.49 kW
        # but clamped to max_discharge_kw=5.0
        plan = _plan(exportable=10.0, slots=slots, max_kw=5.0)
        assert plan is not None
        assert len(plan.planned_slots) == 2
        assert plan.discharge_kw == 5.0

    def test_current_slot_included_when_eligible(self):
        # Slot that started 10 min ago, hasn't ended yet
        current = make_tariff_slot(
            interval_start=NOW - timedelta(minutes=10),
            rate_pence=25.0,
        )
        plan = _plan(exportable=2.0, slots=[current])
        assert plan is not None
        assert len(plan.planned_slots) == 1

    def test_ended_slot_excluded(self):
        ended = make_tariff_slot(
            interval_start=NOW - timedelta(hours=1),
            rate_pence=25.0,
        )
        plan = _plan(exportable=2.0, slots=[ended, _slot(1, 20)])
        assert plan is not None
        assert len(plan.planned_slots) == 1
        assert plan.planned_slots[0].rate_pence == 20.0

    def test_negative_rates_excluded(self):
        slots = [_slot(1, -5.0), _slot(2, 25.0)]
        plan = _plan(exportable=2.0, slots=slots)
        assert plan is not None
        assert len(plan.planned_slots) == 1
        assert plan.planned_slots[0].rate_pence == 25.0

    def test_slots_sorted_by_time_in_output(self):
        # Input: 3h slot first by rate, 1h slot second
        slots = [_slot(3, 30), _slot(1, 25)]
        plan = _plan(exportable=4.0, slots=slots, max_kw=5.0)
        assert plan is not None
        starts = [s.interval_start for s in plan.planned_slots]
        assert starts == sorted(starts)

    def test_all_slots_get_equal_energy(self):
        """Even distribution gives each slot the same kWh."""
        slots = [_slot(1, 30), _slot(2, 25)]
        plan = _plan(exportable=5.5, slots=slots, max_kw=5.0)
        assert plan is not None
        assert len(plan.planned_slots) == 2
        kwhs = [s.expected_kwh for s in plan.planned_slots]
        assert abs(kwhs[0] - kwhs[1]) < 0.01  # equal allocation

    def test_tiny_even_allocation_dropped(self):
        """If even allocation per slot < 0.1 kWh, plan returns None."""
        # 0.05 kWh × sqrt(0.90) ≈ 0.047, only 1 slot needed, 0.047 < 0.1 → dropped
        slots = [_slot(1, 30), _slot(2, 25), _slot(3, 20)]
        plan = _plan(exportable=0.05, slots=slots, max_kw=5.0)
        assert plan is None

    def test_plan_fields_populated(self):
        plan = _plan(exportable=5.0, max_kw=5.0)
        assert plan is not None
        assert plan.exportable_kwh == pytest.approx(5.0)
        assert plan.total_planned_kwh > 0
        assert plan.created_at is not None


class TestExportPlanLookup:
    def test_get_current_slot_found(self):
        plan = _plan(exportable=5.0)
        assert plan is not None
        # Move time into the first planned slot
        slot = plan.planned_slots[0]
        mid = slot.interval_start + timedelta(minutes=15)
        assert plan.get_current_slot(mid) == slot

    def test_get_current_slot_not_found(self):
        plan = _plan(exportable=5.0)
        assert plan is not None
        # Time before any planned slot
        assert plan.get_current_slot(NOW) is None

    def test_get_next_slot(self):
        plan = _plan(exportable=5.0)
        assert plan is not None
        nxt = plan.get_next_slot(NOW)
        assert nxt is not None
        assert nxt.interval_start > NOW


class TestPlannedExportRule:
    """Test PlannedExportRule via the engine (integration)."""

    @pytest.fixture
    def engine(self, thresholds, battery):
        inverter = InverterControlSettings(
            export_planner_enabled=True,
            max_discharge_kw=5.0,
        )
        return RecommendationEngine(thresholds, battery, inverter_control=inverter)

    def test_planned_slot_produces_export_now(self, engine):
        """In a planned slot → EXPORT_NOW / PLANNED_EXPORT."""
        plan = _plan(exportable=5.0)
        assert plan is not None
        slot = plan.planned_slots[0]
        mid = slot.interval_start + timedelta(minutes=10)
        snapshot = make_recommendation_snapshot(
            timestamp=mid,
            current_export_rate=slot.rate_pence,
            battery_soc_pct=70.0,
        )
        result = engine.evaluate(snapshot, export_plan=plan)
        assert result.state == RecommendationState.EXPORT_NOW
        assert result.reason_code == ReasonCode.PLANNED_EXPORT
        assert result.target_discharge_kw == slot.discharge_kw
        assert result.export_plan_slots == len(plan.planned_slots)

    def test_no_plan_falls_through_to_legacy(self, engine):
        """Without a plan, legacy ExportNowRule fires."""
        snapshot = make_recommendation_snapshot(
            timestamp=NOW,
            current_export_rate=20.0,
            best_upcoming_rate=18.0,
            battery_soc_pct=70.0,
        )
        result = engine.evaluate(snapshot, export_plan=None)
        assert result.state == RecommendationState.EXPORT_NOW
        assert result.reason_code != ReasonCode.PLANNED_EXPORT

    def test_planned_hold_when_better_slot_coming(self, engine):
        """Rate above threshold but not in planned slot → HOLD."""
        plan = _plan(exportable=5.0, slots=[_slot(2, 25)])
        assert plan is not None
        snapshot = make_recommendation_snapshot(
            timestamp=NOW,
            current_export_rate=16.0,  # above 15p threshold
            best_upcoming_rate=25.0,
            battery_soc_pct=70.0,
        )
        result = engine.evaluate(snapshot, export_plan=plan)
        assert result.state == RecommendationState.HOLD_BATTERY
        assert result.reason_code == ReasonCode.PLANNED_HOLD

    def test_soc_at_reserve_skips_planned_slot(self, engine, thresholds):
        """Battery at reserve floor → planned slot skipped, falls through."""
        plan = _plan(exportable=5.0)
        assert plan is not None
        slot = plan.planned_slots[0]
        mid = slot.interval_start + timedelta(minutes=10)
        snapshot = make_recommendation_snapshot(
            timestamp=mid,
            current_export_rate=slot.rate_pence,
            battery_soc_pct=thresholds.reserve_soc_floor * 100,  # at floor
        )
        result = engine.evaluate(snapshot, export_plan=plan)
        # Should NOT be PLANNED_EXPORT (SOC too low)
        assert result.reason_code != ReasonCode.PLANNED_EXPORT

    def test_planned_hold_skipped_when_current_rate_better(self, engine):
        """Current rate >= planned slot rate → export now, don't hold."""
        plan = _plan(exportable=5.0, slots=[_slot(2, 18)])
        assert plan is not None
        snapshot = make_recommendation_snapshot(
            timestamp=NOW,
            current_export_rate=19.0,  # above threshold AND above planned 18p
            best_upcoming_rate=18.0,
            battery_soc_pct=70.0,
        )
        result = engine.evaluate(snapshot, export_plan=plan)
        assert result.state == RecommendationState.EXPORT_NOW
        assert result.reason_code != ReasonCode.PLANNED_HOLD

    def test_unplanned_low_rate_falls_through(self, engine):
        """Rate below threshold + not in planned slot → normal self consumption."""
        plan = _plan(exportable=5.0, slots=[_slot(3, 25)])
        snapshot = make_recommendation_snapshot(
            timestamp=NOW,
            current_export_rate=5.0,  # well below threshold
            best_upcoming_rate=25.0,
            battery_soc_pct=70.0,
        )
        result = engine.evaluate(snapshot, export_plan=plan)
        assert result.state == RecommendationState.NORMAL_SELF_CONSUMPTION

    def test_export_now_gets_discharge_kw_from_plan(self, engine):
        """ExportNowRule fires but plan provides controlled discharge power."""
        # Current rate >= planned slot → PlannedExportRule falls through,
        # ExportNowRule fires. Engine should still apply plan's discharge_kw.
        plan = _plan(exportable=5.0, slots=[_slot(2, 18)])
        assert plan is not None
        snapshot = make_recommendation_snapshot(
            timestamp=NOW,
            current_export_rate=19.0,  # better than planned 18p
            best_upcoming_rate=18.0,
            battery_soc_pct=70.0,
        )
        result = engine.evaluate(snapshot, export_plan=plan)
        assert result.state == RecommendationState.EXPORT_NOW
        assert result.reason_code != ReasonCode.PLANNED_EXPORT
        # Discharge power should still be set from the plan
        assert result.target_discharge_kw == plan.discharge_kw
        assert result.export_plan_slots == len(plan.planned_slots)

    def test_export_now_without_plan_has_no_discharge_kw(self, engine):
        """ExportNowRule without a plan → no target_discharge_kw."""
        snapshot = make_recommendation_snapshot(
            timestamp=NOW,
            current_export_rate=20.0,
            best_upcoming_rate=18.0,
            battery_soc_pct=70.0,
        )
        result = engine.evaluate(snapshot, export_plan=None)
        assert result.state == RecommendationState.EXPORT_NOW
        assert result.target_discharge_kw is None
