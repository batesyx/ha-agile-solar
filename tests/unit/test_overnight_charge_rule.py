"""Tests for OvernightChargeRule boundary conditions."""

from datetime import datetime, timezone
from copy import replace

import pytest

from octopus_export_optimizer.recommendation.rules import OvernightChargeRule
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from tests.factories import make_recommendation_snapshot


class TestOvernightChargeRule:
    """OvernightChargeRule: cheap window 23:30-05:30, target SoC 95%."""

    @pytest.fixture
    def rule(self, thresholds, battery):
        return OvernightChargeRule(
            thresholds, battery,
            cheap_rate_start_hour=23.5,
            cheap_rate_end_hour=5.5,
            target_soc_pct=0.95,
        )

    def test_before_window_2329(self, rule):
        """23:29 — just before window, should NOT fire."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=50.0,
            timestamp=datetime(2026, 3, 10, 23, 29, tzinfo=timezone.utc),
        )
        assert rule.evaluate(snap) is None

    def test_at_window_start_2330(self, rule):
        """23:30 — exactly at window start, should fire."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=50.0,
            timestamp=datetime(2026, 3, 10, 23, 30, tzinfo=timezone.utc),
        )
        result = rule.evaluate(snap)
        assert result is not None
        assert result.state == RecommendationState.CHARGE_FOR_LATER_EXPORT
        assert result.reason_code == ReasonCode.OVERNIGHT_CHARGE_STRATEGY

    def test_midnight_in_window(self, rule):
        """00:00 — midnight, inside window."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=50.0,
            timestamp=datetime(2026, 3, 11, 0, 0, tzinfo=timezone.utc),
        )
        result = rule.evaluate(snap)
        assert result is not None
        assert result.state == RecommendationState.CHARGE_FOR_LATER_EXPORT

    def test_near_window_end_0529(self, rule):
        """05:29 — just before window end, should fire."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=50.0,
            timestamp=datetime(2026, 3, 11, 5, 29, tzinfo=timezone.utc),
        )
        result = rule.evaluate(snap)
        assert result is not None

    def test_at_window_end_0530(self, rule):
        """05:30 — at window end, should NOT fire (uses < comparison)."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=50.0,
            timestamp=datetime(2026, 3, 11, 5, 30, tzinfo=timezone.utc),
        )
        assert rule.evaluate(snap) is None

    def test_after_window_0531(self, rule):
        """05:31 — after window, should NOT fire."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=50.0,
            timestamp=datetime(2026, 3, 11, 5, 31, tzinfo=timezone.utc),
        )
        assert rule.evaluate(snap) is None

    def test_already_charged_at_target(self, rule):
        """SoC at 95% target — returns None (already charged)."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=95.0,
            timestamp=datetime(2026, 3, 11, 0, 0, tzinfo=timezone.utc),
        )
        assert rule.evaluate(snap) is None

    def test_just_below_target_fires(self, rule):
        """SoC at 94% — should fire."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=94.0,
            timestamp=datetime(2026, 3, 11, 0, 0, tzinfo=timezone.utc),
        )
        result = rule.evaluate(snap)
        assert result is not None
        assert result.state == RecommendationState.CHARGE_FOR_LATER_EXPORT

    def test_none_soc_returns_none(self, rule):
        """No battery data — returns None."""
        snap = make_recommendation_snapshot(
            battery_soc_pct=None,
            timestamp=datetime(2026, 3, 11, 0, 0, tzinfo=timezone.utc),
        )
        assert rule.evaluate(snap) is None


class TestOvernightChargeDynamicTarget:
    """OvernightChargeRule with solar-aware dynamic target."""

    @pytest.fixture
    def rule(self, thresholds, battery):
        return OvernightChargeRule(
            thresholds, battery,
            cheap_rate_start_hour=23.5,
            cheap_rate_end_hour=5.5,
            target_soc_pct=0.95,
        )

    def _snap(self, soc: float, overnight_target: float | None = None):
        snap = make_recommendation_snapshot(
            battery_soc_pct=soc,
            timestamp=datetime(2026, 3, 11, 0, 0, tzinfo=timezone.utc),
        )
        return replace(snap, overnight_charge_target_pct=overnight_target)

    def test_dynamic_target_used_when_present(self, rule):
        """SoC at 45%, dynamic target at 50% — should fire."""
        snap = self._snap(soc=45.0, overnight_target=0.50)
        result = rule.evaluate(snap)
        assert result is not None
        assert result.state == RecommendationState.CHARGE_FOR_LATER_EXPORT
        assert "solar-aware" in result.explanation

    def test_dynamic_target_at_target_still_fires(self, rule):
        """SoC at 50%, dynamic target at 50% — still fires to set max_soc cap."""
        snap = self._snap(soc=50.0, overnight_target=0.50)
        result = rule.evaluate(snap)
        assert result is not None
        assert "capped at" in result.explanation

    def test_dynamic_target_above_soc_still_fires(self, rule):
        """SoC at 70%, dynamic target at 80% — should fire."""
        snap = self._snap(soc=70.0, overnight_target=0.80)
        result = rule.evaluate(snap)
        assert result is not None

    def test_fallback_to_static_when_no_dynamic(self, rule):
        """No dynamic target — falls back to static 95%."""
        snap = self._snap(soc=90.0, overnight_target=None)
        result = rule.evaluate(snap)
        assert result is not None
        assert "solar-aware" not in result.explanation
        assert "95%" in result.explanation

    def test_fallback_static_at_target(self, rule):
        """SoC at 95%, no dynamic target — should NOT fire."""
        snap = self._snap(soc=95.0, overnight_target=None)
        assert rule.evaluate(snap) is None
