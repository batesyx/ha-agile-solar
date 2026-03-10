"""Tests for the recommendation engine."""

from datetime import datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.config.settings import (
    BatterySettings,
    InverterControlSettings,
    ThresholdSettings,
)
from octopus_export_optimizer.recommendation.engine import RecommendationEngine
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from tests.factories import make_recommendation_snapshot, make_tariff_slot


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
    def test_better_slot_coming_low_battery(self, engine):
        """With low battery, hold for the better slot."""
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=25.0,  # 9p better, above 3p delta
            battery_soc_pct=25.0,
        )
        snapshot.exportable_battery_kwh = 0.5  # Not enough for both slots
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.HOLD_BATTERY

    def test_better_slot_coming_high_battery_exports_anyway(self, engine):
        """With plenty of battery, export now even though better slot exists."""
        snapshot = make_recommendation_snapshot(
            current_export_rate=16.0,
            best_upcoming_rate=25.0,  # 9p better, above 3p delta
            battery_soc_pct=80.0,
        )
        snapshot.exportable_battery_kwh = 6.9  # (0.80 - 0.20) * 11.52
        result = engine.evaluate(snapshot)
        assert result.state == RecommendationState.EXPORT_NOW

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


class TestMaxSocTiming:
    """Test time-gated max_soc: only raise to 100% near peak export."""

    @pytest.fixture
    def soc_engine(self, thresholds, battery):
        inverter = InverterControlSettings(
            high_export_threshold_for_full_charge=20.0,
            full_charge_lead_time_hours=1.5,
        )
        return RecommendationEngine(thresholds, battery, inverter_control=inverter)

    def _make_rates(self, now, offsets_hours, rate=25.0):
        """Create tariff slots at given hour offsets from now."""
        return [
            make_tariff_slot(
                interval_start=now + timedelta(hours=h),
                rate_pence=rate,
            )
            for h in offsets_hours
        ]

    def test_high_rate_far_away_stays_at_90(self, soc_engine):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(timestamp=now)
        rates = self._make_rates(now, [6.0])
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 90

    def test_high_rate_within_lead_time_raises_to_100(self, soc_engine):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(timestamp=now)
        rates = self._make_rates(now, [1.0])
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 100

    def test_high_rate_exactly_at_boundary_raises_to_100(self, soc_engine):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(timestamp=now)
        rates = self._make_rates(now, [1.5])  # exactly at lead time
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 100

    def test_no_high_rates_stays_at_90(self, soc_engine):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(timestamp=now)
        rates = self._make_rates(now, [1.0], rate=10.0)  # below threshold
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 90

    def test_active_slot_raises_to_100(self, soc_engine):
        """Slot that started 10 min ago (negative time_until) → 100%."""
        now = datetime(2026, 3, 10, 9, 10, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(timestamp=now)
        rates = self._make_rates(now, [-10 / 60])  # started 10 min ago
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 100

    def test_multiple_slots_earliest_far_stays_at_90(self, soc_engine):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(timestamp=now)
        rates = self._make_rates(now, [4.0, 6.0])
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 90

    def test_multiple_slots_earliest_close_raises_to_100(self, soc_engine):
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(timestamp=now)
        rates = self._make_rates(now, [1.0, 6.0])
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 100

    def test_solar_generating_rate_delta_justifies_100(self, soc_engine):
        """Solar active, but upcoming rate well above current → 100%."""
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(
            timestamp=now, pv_power_kw=3.0, current_export_rate=11.0,
        )
        rates = self._make_rates(now, [1.0])  # 25p, within lead time
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        # 25 × sqrt(0.90) ≈ 23.7 > 11 → worth it
        assert result.target_max_soc == 100

    def test_solar_generating_marginal_delta_stays_90(self, soc_engine):
        """Solar active, upcoming rate barely above current after losses → 90%."""
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(
            timestamp=now, pv_power_kw=3.0, current_export_rate=20.0,
        )
        # 20p upcoming: 20 × sqrt(0.90) ≈ 18.97 < 20 → not worth it
        rates = self._make_rates(now, [1.0], rate=20.0)
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 90

    def test_no_solar_always_allows_100(self, soc_engine):
        """No solar generation → no opportunity cost → 100%."""
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(
            timestamp=now, pv_power_kw=0.0, current_export_rate=20.0,
        )
        rates = self._make_rates(now, [1.0], rate=20.0)
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 100

    def test_solar_below_threshold_allows_100(self, soc_engine):
        """Solar below 0.5 kW threshold → treated as no solar → 100%."""
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(
            timestamp=now, pv_power_kw=0.3, current_export_rate=20.0,
        )
        rates = self._make_rates(now, [1.0], rate=20.0)
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 100

    def test_no_current_rate_allows_100(self, soc_engine):
        """No current rate data → can't compare → allow 100%."""
        now = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        snapshot = make_recommendation_snapshot(
            timestamp=now, pv_power_kw=3.0, current_export_rate=None,
        )
        rates = self._make_rates(now, [1.0])
        result = soc_engine.evaluate(snapshot, upcoming_12h_rates=rates)
        assert result.target_max_soc == 100
