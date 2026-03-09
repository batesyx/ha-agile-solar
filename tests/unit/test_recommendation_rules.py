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

    def test_skips_when_better_slot_ahead(self, thresholds, battery):
        rule = ExportNowRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0, best_upcoming_rate=25.0
        )
        result = rule.evaluate(snapshot)
        assert result is None

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


class TestNormalSelfConsumptionRule:
    def test_always_fires(self, thresholds, battery):
        rule = NormalSelfConsumptionRule(thresholds, battery)
        snapshot = make_recommendation_snapshot(
            current_export_rate=5.0, best_upcoming_rate=6.0
        )
        result = rule.evaluate(snapshot)
        assert result is not None
        assert result.state == RecommendationState.NORMAL_SELF_CONSUMPTION
