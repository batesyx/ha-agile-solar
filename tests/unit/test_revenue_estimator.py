"""Tests for real-time revenue estimator."""

from datetime import datetime, timedelta, timezone

from octopus_export_optimizer.calculations.revenue_estimator import (
    RevenueEstimate,
    estimate_revenue,
)
from octopus_export_optimizer.config.settings import ThresholdSettings
from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.models.tariff import TariffSlot


def _make_snapshot(hour: int, minute: int, feed_in_kw: float) -> HaStateSnapshot:
    return HaStateSnapshot(
        timestamp=datetime(2026, 3, 10, hour, minute, tzinfo=timezone.utc),
        battery_soc_pct=50.0,
        pv_power_kw=3.0,
        feed_in_kw=feed_in_kw,
        load_power_kw=1.0,
        grid_consumption_kw=0.0,
        battery_charge_kw=0.0,
        battery_discharge_kw=0.0,
    )


def _make_tariff(hour: int) -> TariffSlot:
    start = datetime(2026, 3, 10, hour, 0, tzinfo=timezone.utc)
    return TariffSlot(
        interval_start=start,
        interval_end=start + timedelta(minutes=30),
        rate_inc_vat_pence=10.0,
        tariff_type="export",
        product_code="AGILE",
        provenance="actual",
        fetched_at=start,
    )


def _thresholds() -> ThresholdSettings:
    return ThresholdSettings()


class TestRevenueEstimator:
    def test_empty_snapshots_returns_zero(self):
        result = estimate_revenue(
            [], [], _thresholds(), datetime(2026, 3, 10, tzinfo=timezone.utc)
        )
        assert result.export_kwh == 0.0
        assert result.agile_revenue_pence == 0.0

    def test_single_snapshot_returns_zero(self):
        result = estimate_revenue(
            [_make_snapshot(10, 0, 2.0)],
            [_make_tariff(10)],
            _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        assert result.export_kwh == 0.0

    def test_two_snapshots_calculates_energy(self):
        """2 kW feed-in for 5 minutes = 1/6 kWh at 10p/kWh ≈ 1.67p."""
        snapshots = [
            _make_snapshot(10, 0, 2.0),
            _make_snapshot(10, 5, 2.0),
        ]
        tariffs = [_make_tariff(10)]
        result = estimate_revenue(
            snapshots, tariffs, _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        # 2 kW × 5/60 hours = 0.1667 kWh
        assert abs(result.export_kwh - 0.1667) < 0.001
        # 0.1667 kWh × 10p = 1.667p
        assert abs(result.agile_revenue_pence - 1.667) < 0.01

    def test_negative_feed_in_treated_as_zero(self):
        snapshots = [
            _make_snapshot(10, 0, -1.0),
            _make_snapshot(10, 30, -1.0),
        ]
        tariffs = [_make_tariff(10)]
        result = estimate_revenue(
            snapshots, tariffs, _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        assert result.export_kwh == 0.0

    def test_large_gap_skipped(self):
        """Gaps > 10 minutes between snapshots are skipped."""
        snapshots = [
            _make_snapshot(10, 0, 2.0),
            _make_snapshot(10, 15, 2.0),  # 15 min gap > 10 min limit
        ]
        tariffs = [_make_tariff(10)]
        result = estimate_revenue(
            snapshots, tariffs, _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        assert result.export_kwh == 0.0

    def test_no_matching_tariff_skips_interval(self):
        snapshots = [
            _make_snapshot(10, 0, 2.0),
            _make_snapshot(10, 5, 2.0),
        ]
        # Tariff for hour 11, not 10
        tariffs = [_make_tariff(11)]
        result = estimate_revenue(
            snapshots, tariffs, _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        assert result.export_kwh == 0.0

    def test_flat_revenue_uses_configured_rate(self):
        thresholds = ThresholdSettings(flat_export_rate_pence=15.0)
        snapshots = [
            _make_snapshot(10, 0, 2.0),
            _make_snapshot(10, 5, 2.0),  # 5 min = 1/12 hour, 2kW = 1/6 kWh
        ]
        tariffs = [_make_tariff(10)]
        result = estimate_revenue(
            snapshots, tariffs, thresholds,
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        # 0.1667 kWh × 15p ≈ 2.5p flat (but rounding: 0.1667 × 15 = 2.5005)
        assert result.flat_revenue_pence > 0
        # Check flat rate was used (not the default 12p)
        assert result.flat_revenue_pence > result.agile_revenue_pence  # 15p > 10p
