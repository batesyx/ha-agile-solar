"""Tests for individual recommendation rules."""

from datetime import datetime, timezone

import pytest

from octopus_export_optimizer.config.settings import BatterySettings, ThresholdSettings
from octopus_export_optimizer.recommendation.rules import (
    ChargeForLaterExportRule,
    ExportNowRule,
    HoldBatteryRule,
    InsufficientDataRule,
    NormalSelfConsumptionRule,
)
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from tests.factories import make_recommendation_snapshot


@pytest.fixture
def thresholds_arb():
    """Thresholds with import arbitrage enabled."""
    return ThresholdSettings(allow_import_arbitrage=True)


class TestInsufficientDataRule:
    def test_fires_when_no_rate(self, thresholds, battery):
        rule = InsufficientDataRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=None, upcoming_rates_count=0
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.INSUFFICIENT_DATA

    def test_skips_when_data_present(self, thresholds, battery):
        rule = InsufficientDataRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(current_export_rate=15.0)
        result = rule.evaluate(snapshot)
        assert result is None


class TestExportNowRule:
    def test_fires_on_high_rate(self, thresholds, battery):
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=20.0, best_upcoming_rate=18.0
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.EXPORT_NOW

    def test_skips_below_threshold(self, thresholds, battery):
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=10.0, best_upcoming_rate=8.0
        )
        result = rule.evaluate(snapshot)
        assert result is None

    def test_skips_when_better_slot_ahead_and_low_battery(self, thresholds, battery):
        """With low battery, hold for the better slot instead of exporting."""
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0, best_upcoming_rate=25.0,
            battery_soc_pct=25.0,
        )
        snapshot.exportable_battery_kwh = 0.5  # Below 15% of 11.52 = 1.73 kWh
        result = rule.evaluate(snapshot)
        assert result is None

    def test_exports_at_strong_rate_with_sufficient_battery_despite_better_slot(
        self, thresholds, battery
    ):
        """Scenario A: 22p rate, 28p coming, but 92% SoC — export at both."""
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=22.0, best_upcoming_rate=28.0,
            battery_soc_pct=92.0,
        )
        snapshot.exportable_battery_kwh = 8.3  # (0.92 - 0.20) * 11.52
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.EXPORT_NOW
        assert result.reason_code == ReasonCode.HIGH_RATE_WITH_BATTERY
        assert "enough capacity for both" in result.explanation

    def test_holds_for_better_slot_when_battery_low(self, thresholds, battery):
        """Strong rate but battery can only serve one slot — hold for the best."""
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=22.0, best_upcoming_rate=28.0,
            battery_soc_pct=25.0,
        )
        snapshot.exportable_battery_kwh = 0.58  # (0.25 - 0.20) * 11.52
        result = rule.evaluate(snapshot)
        assert result is None  # Defers to HoldBatteryRule

    def test_low_soc_with_solar_feedin(self, thresholds, battery):
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=20.0,
            best_upcoming_rate=18.0,
            battery_soc_pct=10.0,  # below minimum_soc_for_export (35%)
            feed_in_kw=2.0,
        )
        # Update exportable to reflect low SOC
        snapshot.exportable_battery_kwh = 0.0
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.reason_code == ReasonCode.HIGH_RATE_SOLAR_EXPORT

    def test_rate_exactly_at_threshold_fires(self, thresholds, battery):
        """Rate == threshold (15.0p) fires because check is < not <=."""
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=15.0, best_upcoming_rate=14.0,
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.EXPORT_NOW

    def test_negative_rate_does_not_fire(self, thresholds, battery):
        """Negative export rate — should not export."""
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=-2.0, best_upcoming_rate=-0.5,
        )
        result = rule.evaluate(snapshot)
        assert result is None

    def test_soc_exactly_at_minimum_for_export(self, thresholds, battery):
        """SoC at 35% (minimum_soc_for_export=0.35) — should still export (uses <)."""
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=20.0, best_upcoming_rate=18.0,
            battery_soc_pct=35.0,
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.EXPORT_NOW

    def test_exportable_exactly_at_capacity_boundary(self, thresholds, battery):
        """Exportable at exactly 15% of capacity (1.728) — fires (uses >)."""
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=20.0, best_upcoming_rate=28.0,
            battery_soc_pct=40.0,
        )
        # 15% of 11.52 = 1.728 — exactly at boundary, should NOT fire (> not >=)
        snapshot.exportable_battery_kwh = 1.728
        result = rule.evaluate(snapshot)
        assert result is None  # Not > 1.728, so holds for better slot


class TestHoldBatteryRule:
    def test_fires_when_better_slot_coming(self, thresholds, battery):
        rule = HoldBatteryRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=25.0,
            battery_soc_pct=80.0,
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.HOLD_BATTERY

    def test_skips_when_rate_too_low(self, thresholds, battery):
        rule = HoldBatteryRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=3.0,  # below 50% of threshold
            best_upcoming_rate=20.0,
        )
        result = rule.evaluate(snapshot)
        assert result is None

    def test_includes_generation_note(self, thresholds, battery):
        rule = HoldBatteryRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=25.0,
            battery_soc_pct=80.0,
            remaining_generation=0.6,  # above 0.3 threshold
        )
        result = rule.evaluate(snapshot)
        assert "generation" in result.explanation.lower()

    def test_delta_exactly_at_threshold_does_not_fire(self, thresholds, battery):
        """Delta == better_slot_delta_pence (3.0p) — does NOT fire (uses <=)."""
        rule = HoldBatteryRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=17.0,
            best_upcoming_rate=20.0,  # delta = 3.0
            battery_soc_pct=80.0,
        )
        result = rule.evaluate(snapshot)
        assert result is None

    def test_soc_at_reserve_floor_still_holds(self, thresholds, battery):
        """SoC at reserve floor (20%) — still holds (0.20 is not < 0.20)."""
        rule = HoldBatteryRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=25.0,
            battery_soc_pct=20.0,
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.HOLD_BATTERY

    def test_soc_below_reserve_floor_does_not_fire(self, thresholds, battery):
        """SoC below reserve floor (19%) — nothing to hold."""
        rule = HoldBatteryRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=25.0,
            battery_soc_pct=19.0,
        )
        result = rule.evaluate(snapshot)
        assert result is None

    def test_no_battery_data_tariff_only_hold(self, thresholds, battery):
        """No battery SoC — produces tariff-only hold recommendation."""
        rule = HoldBatteryRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=25.0,
            battery_soc_pct=None,
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.HOLD_BATTERY
        assert result.reason_code == ReasonCode.BETTER_SLOT_COMING


class TestChargeForLaterExportRule:
    def test_skips_when_arbitrage_disabled(self, thresholds, battery):
        rule = ChargeForLaterExportRule(thresholds, battery)
        snapshot = make_recommendation_snapshot()
        result = rule.evaluate(snapshot)
        assert result is None

    def test_fires_with_cheap_import_and_high_upcoming(self, thresholds_arb, battery):
        rule = ChargeForLaterExportRule(thresholds_arb, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=5.0,
            best_upcoming_rate=30.0,  # >= 1.5x threshold (22.5p)
            battery_soc_pct=40.0,
        )
        snapshot.current_import_rate_pence = 5.0  # cheap
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.CHARGE_FOR_LATER_EXPORT

    def test_none_import_rate_returns_none(self, thresholds_arb, battery):
        rule = ChargeForLaterExportRule(thresholds_arb, battery)
        snapshot = make_recommendation_snapshot(
            best_upcoming_rate=30.0, battery_soc_pct=40.0,
        )
        snapshot.current_import_rate_pence = None
        result = rule.evaluate(snapshot)
        assert result is None

    def test_none_battery_soc_returns_none(self, thresholds_arb, battery):
        rule = ChargeForLaterExportRule(thresholds_arb, battery)
        snapshot = make_recommendation_snapshot(
            best_upcoming_rate=30.0, battery_soc_pct=None,
        )
        snapshot.current_import_rate_pence = 5.0
        result = rule.evaluate(snapshot)
        assert result is None

    def test_none_upcoming_rate_returns_none(self, thresholds_arb, battery):
        rule = ChargeForLaterExportRule(thresholds_arb, battery)
        snapshot = make_recommendation_snapshot(
            best_upcoming_rate=None, battery_soc_pct=40.0,
        )
        snapshot.current_import_rate_pence = 5.0
        result = rule.evaluate(snapshot)
        assert result is None

    def test_soc_at_95_does_not_fire(self, thresholds_arb, battery):
        """SoC at 95% — no headroom, should NOT fire (uses < 0.95)."""
        rule = ChargeForLaterExportRule(thresholds_arb, battery)
        snapshot = make_recommendation_snapshot(
            best_upcoming_rate=30.0, battery_soc_pct=95.0,
        )
        snapshot.current_import_rate_pence = 5.0
        result = rule.evaluate(snapshot)
        assert result is None


class TestNormalSelfConsumptionRule:
    def test_always_fires(self, thresholds, battery):
        rule = NormalSelfConsumptionRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=5.0, best_upcoming_rate=6.0
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.NORMAL_SELF_CONSUMPTION
