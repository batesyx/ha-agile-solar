"""Tests for the recommendation engine."""

from datetime import datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.config.settings import BatterySettings, ThresholdSettings
from octopus_export_optimizer.recommendation.engine import RecommendationEngine
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from tests.factories import make_recommendation_snapshot


@pytest.fixture
def engine(thresholds, battery):
    return RecommendationEngine(thresholds, battery)


class TestInsufficientData:
    def test_no_tariff_data(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=None, best_upcoming_rate=None, upcoming_rates_count=0
        )
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.INSUFFICIENT_DATA
        assert result.reason_code == ReasonCode.NO_TARIFF_DATA

    def test_no_upcoming_rates(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=15.0, best_upcoming_rate=None, upcoming_rates_count=0
        )
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.INSUFFICIENT_DATA


class TestExportNow:
    def test_high_rate_no_better_slot(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=20.0,
            best_upcoming_rate=18.0,  # not meaningfully better
            battery_soc_pct=70.0,
        )
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.EXPORT_NOW
        assert result.battery_aware is True

    def test_high_rate_tariff_only(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=20.0,
            best_upcoming_rate=18.0,
            battery_soc_pct=None,  # no battery data
        )
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.EXPORT_NOW
        assert result.battery_aware is False

    def test_rate_below_threshold(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=10.0,
            best_upcoming_rate=8.0,
        )
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.NORMAL_SELF_CONSUMPTION


class TestHoldBattery:
    def test_better_slot_coming(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=25.0,  # 9p better, above 3p delta
            battery_soc_pct=80.0,
        )
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.HOLD_BATTERY

    def test_marginally_better_slot_not_held(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=17.0,  # only 1p better, below 3p delta
            battery_soc_pct=80.0,
        )
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.EXPORT_NOW


class TestNormalSelfConsumption:
    def test_low_rate(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=5.0,
            best_upcoming_rate=6.0,
        )
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.NORMAL_SELF_CONSUMPTION
        assert result.reason_code == ReasonCode.LOW_EXPORT_RATE


class TestDeterminism:
    def test_same_input_same_output(self, engine):
        now = datetime(2026, 3, 9, 14, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(
            current_export_rate=20.0,
            best_upcoming_rate=18.0,
            battery_soc_pct=70.0,
            timestamp=now,
        )

        results = [engine.evaluate(snapshot) for _ in range(10)]

        states = {r.state for r in results}
        reasons = {r.reason_code for r in results}
        assert len(states) == 1
        assert len(reasons) == 1


class TestExplanations:
    def test_explanation_is_human_readable(self, engine):
        snapshot = make_recommendation_snapshot(
            current_export_rate=22.5,
            best_upcoming_rate=18.0,
            battery_soc_pct=78.0,
        )
        result = engine.evaluate(snapshot)
        assert "22.5" in result.explanation
        assert "p/kWh" in result.explanation
        assert len(result.explanation) > 20

    def test_input_snapshot_id_is_preserved(self, engine):
        snapshot = make_recommendation_snapshot()
        result = engine.evaluate(snapshot)
        assert result.input_snapshot_id == snapshot.id
