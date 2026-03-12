"""Tests for real-time revenue estimator."""

from datetime import datetime, timedelta, timezone

import pytest

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


def _make_full_snapshot(
    hour: int,
    minute: int,
    feed_in_kw: float = 0.0,
    load_power_kw: float = 1.0,
    pv_power_kw: float = 0.0,
    battery_charge_kw: float = 0.0,
    battery_discharge_kw: float = 0.0,
) -> HaStateSnapshot:
    return HaStateSnapshot(
        timestamp=datetime(2026, 3, 10, hour, minute, tzinfo=timezone.utc),
        battery_soc_pct=50.0,
        pv_power_kw=pv_power_kw,
        feed_in_kw=feed_in_kw,
        load_power_kw=load_power_kw,
        grid_consumption_kw=0.0,
        battery_charge_kw=battery_charge_kw,
        battery_discharge_kw=battery_discharge_kw,
    )


def _make_import_tariff(hour: int, rate: float = 7.5) -> TariffSlot:
    start = datetime(2026, 3, 10, hour, 0, tzinfo=timezone.utc)
    return TariffSlot(
        interval_start=start,
        interval_end=start + timedelta(minutes=30),
        rate_inc_vat_pence=rate,
        tariff_type="import",
        product_code="AGILE-FLEX",
        provenance="actual",
        fetched_at=start,
    )


class TestImportCostEstimation:
    def test_grid_consumption_estimated(self):
        """Load 2kW, no PV, no battery → 2kW from grid."""
        snapshots = [
            _make_full_snapshot(10, 0, load_power_kw=2.0),
            _make_full_snapshot(10, 5, load_power_kw=2.0),
        ]
        import_tariffs = [_make_import_tariff(10, rate=10.0)]
        result = estimate_revenue(
            snapshots, [_make_tariff(10)], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
            import_tariff_slots=import_tariffs,
        )
        # 2kW × 5/60h = 0.1667 kWh × 10p = 1.667p
        assert result.import_kwh > 0
        assert abs(result.import_kwh - 0.1667) < 0.001
        assert abs(result.import_cost_pence - 1.667) < 0.01

    def test_pv_covers_load_no_import(self):
        """Load 1kW, PV 3kW → no grid import."""
        snapshots = [
            _make_full_snapshot(10, 0, load_power_kw=1.0, pv_power_kw=3.0),
            _make_full_snapshot(10, 5, load_power_kw=1.0, pv_power_kw=3.0),
        ]
        import_tariffs = [_make_import_tariff(10)]
        result = estimate_revenue(
            snapshots, [_make_tariff(10)], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
            import_tariff_slots=import_tariffs,
        )
        assert result.import_kwh == 0.0
        assert result.import_cost_pence == 0.0

    def test_grid_charging_included_in_import(self):
        """Overnight grid charging: load 1kW + battery_charge 3kW, no PV → 4kW from grid."""
        snapshots = [
            _make_full_snapshot(2, 0, load_power_kw=1.0, battery_charge_kw=3.0),
            _make_full_snapshot(2, 5, load_power_kw=1.0, battery_charge_kw=3.0),
        ]
        import_tariffs = [
            TariffSlot(
                interval_start=datetime(2026, 3, 10, 2, 0, tzinfo=timezone.utc),
                interval_end=datetime(2026, 3, 10, 2, 30, tzinfo=timezone.utc),
                rate_inc_vat_pence=7.5,
                tariff_type="import",
                product_code="AGILE-FLEX",
                provenance="actual",
                fetched_at=datetime(2026, 3, 10, 2, 0, tzinfo=timezone.utc),
            )
        ]
        export_tariffs = [
            TariffSlot(
                interval_start=datetime(2026, 3, 10, 2, 0, tzinfo=timezone.utc),
                interval_end=datetime(2026, 3, 10, 2, 30, tzinfo=timezone.utc),
                rate_inc_vat_pence=5.0,
                tariff_type="export",
                product_code="AGILE",
                provenance="actual",
                fetched_at=datetime(2026, 3, 10, 2, 0, tzinfo=timezone.utc),
            )
        ]
        result = estimate_revenue(
            snapshots, export_tariffs, _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
            import_tariff_slots=import_tariffs,
        )
        # 4kW × 5/60h = 0.3333 kWh × 7.5p = 2.5p
        assert abs(result.import_kwh - 0.3333) < 0.001
        assert abs(result.import_cost_pence - 2.5) < 0.01

    def test_no_import_tariffs_skips_import_cost(self):
        """Without import tariffs, import cost stays at zero."""
        snapshots = [
            _make_full_snapshot(10, 0, load_power_kw=2.0),
            _make_full_snapshot(10, 5, load_power_kw=2.0),
        ]
        result = estimate_revenue(
            snapshots, [_make_tariff(10)], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        assert result.import_kwh == 0.0
        assert result.import_cost_pence == 0.0


class TestChargingOpportunityCost:
    def test_solar_charging_has_opportunity_cost(self):
        """Battery charging 2kW from 3kW PV at 10p/kWh export rate."""
        snapshots = [
            _make_full_snapshot(
                10, 0, pv_power_kw=3.0, battery_charge_kw=2.0, feed_in_kw=1.0,
            ),
            _make_full_snapshot(
                10, 5, pv_power_kw=3.0, battery_charge_kw=2.0, feed_in_kw=1.0,
            ),
        ]
        tariffs = [_make_tariff(10)]  # 10p/kWh export
        result = estimate_revenue(
            snapshots, tariffs, _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        # min(battery_charge=2, pv=3) = 2kW × 5/60h = 0.1667 kWh × 10p = 1.667p
        assert abs(result.charging_opportunity_cost_pence - 1.667) < 0.01

    def test_no_pv_no_opportunity_cost(self):
        """Battery charging from grid (no PV) → zero opportunity cost."""
        snapshots = [
            _make_full_snapshot(
                10, 0, pv_power_kw=0.0, battery_charge_kw=2.0,
            ),
            _make_full_snapshot(
                10, 5, pv_power_kw=0.0, battery_charge_kw=2.0,
            ),
        ]
        tariffs = [_make_tariff(10)]
        result = estimate_revenue(
            snapshots, tariffs, _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        assert result.charging_opportunity_cost_pence == 0.0

    def test_zero_export_rate_no_opportunity_cost(self):
        """Even with solar charging, zero export rate → no opportunity cost."""
        zero_rate_tariff = TariffSlot(
            interval_start=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
            interval_end=datetime(2026, 3, 10, 10, 30, tzinfo=timezone.utc),
            rate_inc_vat_pence=0.0,
            tariff_type="export",
            product_code="AGILE",
            provenance="actual",
            fetched_at=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
        )
        snapshots = [
            _make_full_snapshot(
                10, 0, pv_power_kw=3.0, battery_charge_kw=2.0,
            ),
            _make_full_snapshot(
                10, 5, pv_power_kw=3.0, battery_charge_kw=2.0,
            ),
        ]
        result = estimate_revenue(
            snapshots, [zero_rate_tariff], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        assert result.charging_opportunity_cost_pence == 0.0

    def test_net_revenue_and_true_profit(self):
        """True profit = agile_revenue - import_cost - opportunity_cost."""
        snapshots = [
            _make_full_snapshot(
                10, 0, feed_in_kw=2.0, load_power_kw=1.0,
                pv_power_kw=3.0, battery_charge_kw=1.0,
            ),
            _make_full_snapshot(
                10, 5, feed_in_kw=2.0, load_power_kw=1.0,
                pv_power_kw=3.0, battery_charge_kw=1.0,
            ),
        ]
        export_tariffs = [_make_tariff(10)]  # 10p/kWh
        import_tariffs = [_make_import_tariff(10, rate=7.5)]

        result = estimate_revenue(
            snapshots, export_tariffs, _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
            import_tariff_slots=import_tariffs,
        )
        # Export: 2kW × 5/60h = 0.1667 kWh × 10p = 1.667p
        # Import: (load(1) + bat_chg(1)) - pv(3) - bat_dis(0) = 0 → clamped to 0
        # Opportunity: min(bat_chg=1, pv=3) = 1kW × 5/60h = 0.0833 × 10p = 0.833p
        assert result.net_revenue_pence == pytest.approx(
            result.agile_revenue_pence - result.import_cost_pence, abs=0.01
        )
        assert result.true_profit_pence == pytest.approx(
            result.net_revenue_pence - result.charging_opportunity_cost_pence, abs=0.01
        )


class TestFlatBaselineSolarExcess:
    """Flat baseline should use solar excess (pv - load), not actual feed-in."""

    def test_normal_export_flat_matches_solar_excess(self):
        """When all solar excess goes to grid, flat ≈ old calculation."""
        # pv=3, load=1 → solar_excess=2, feed_in=2 → same result
        snapshots = [
            _make_full_snapshot(10, 0, feed_in_kw=2.0, pv_power_kw=3.0, load_power_kw=1.0),
            _make_full_snapshot(10, 5, feed_in_kw=2.0, pv_power_kw=3.0, load_power_kw=1.0),
        ]
        result = estimate_revenue(
            snapshots, [_make_tariff(10)], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        # solar_excess = 2kW × 5/60h = 0.1667 kWh × 12p = 2.0p
        assert result.flat_export_kwh == pytest.approx(0.1667, abs=0.001)
        assert result.flat_revenue_pence == pytest.approx(result.flat_export_kwh * 12.0, abs=0.01)

    def test_charge_plan_solar_stored_flat_still_counts(self):
        """Charge planner absorbs solar: feed_in=0 but flat counts full solar excess."""
        # pv=3, load=1, battery_charge=2, feed_in=0 (all solar to battery)
        snapshots = [
            _make_full_snapshot(
                10, 0, feed_in_kw=0.0, pv_power_kw=3.0,
                load_power_kw=1.0, battery_charge_kw=2.0,
            ),
            _make_full_snapshot(
                10, 5, feed_in_kw=0.0, pv_power_kw=3.0,
                load_power_kw=1.0, battery_charge_kw=2.0,
            ),
        ]
        result = estimate_revenue(
            snapshots, [_make_tariff(10)], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        # Agile export = 0 (feed_in=0)
        assert result.export_kwh == 0.0
        assert result.agile_revenue_pence == 0.0

        # Flat baseline uses solar excess: pv(3) - load(1) = 2kW
        # 2kW × 5/60h = 0.1667 kWh × 12p = 2.0p
        assert result.flat_export_kwh == pytest.approx(0.1667, abs=0.001)
        assert result.flat_revenue_pence == pytest.approx(2.0, abs=0.1)

        # Uplift is negative (Agile earned 0, flat would have earned 2p)
        assert result.uplift_pence < 0

    def test_force_discharge_no_solar_flat_is_zero(self):
        """Force Discharge at night: battery exports but flat wouldn't."""
        # pv=0, load=0.5, battery_discharge=5, feed_in=4.5
        snapshots = [
            _make_full_snapshot(
                18, 0, feed_in_kw=4.5, pv_power_kw=0.0,
                load_power_kw=0.5, battery_discharge_kw=5.0,
            ),
            _make_full_snapshot(
                18, 5, feed_in_kw=4.5, pv_power_kw=0.0,
                load_power_kw=0.5, battery_discharge_kw=5.0,
            ),
        ]
        tariff = TariffSlot(
            interval_start=datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc),
            interval_end=datetime(2026, 3, 10, 18, 30, tzinfo=timezone.utc),
            rate_inc_vat_pence=20.0,
            tariff_type="export",
            product_code="AGILE",
            provenance="actual",
            fetched_at=datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc),
        )
        result = estimate_revenue(
            snapshots, [tariff], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        # Agile: 4.5kW × 5/60h = 0.375 kWh × 20p = 7.5p
        assert result.export_kwh == pytest.approx(0.375, abs=0.001)
        assert result.agile_revenue_pence == pytest.approx(7.5, abs=0.01)

        # Flat: solar_excess = max(0, 0 - 0.5) = 0 → flat earns nothing
        assert result.flat_export_kwh == 0.0
        assert result.flat_revenue_pence == 0.0

        # Uplift is positive (Agile earned 7.5p, flat would earn 0)
        assert result.uplift_pence > 0

    def test_no_pv_data_flat_is_zero(self):
        """When PV data is None, solar excess defaults to 0."""
        snapshots = [
            HaStateSnapshot(
                timestamp=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc),
                battery_soc_pct=50.0,
                pv_power_kw=None,
                feed_in_kw=2.0,
                load_power_kw=None,
                grid_consumption_kw=0.0,
                battery_charge_kw=0.0,
                battery_discharge_kw=0.0,
            ),
            HaStateSnapshot(
                timestamp=datetime(2026, 3, 10, 10, 5, tzinfo=timezone.utc),
                battery_soc_pct=50.0,
                pv_power_kw=None,
                feed_in_kw=2.0,
                load_power_kw=None,
                grid_consumption_kw=0.0,
                battery_charge_kw=0.0,
                battery_discharge_kw=0.0,
            ),
        ]
        result = estimate_revenue(
            snapshots, [_make_tariff(10)], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        # Agile still uses feed_in
        assert result.export_kwh > 0
        # Flat = 0 (no PV data → solar excess = 0)
        assert result.flat_export_kwh == 0.0
        assert result.flat_revenue_pence == 0.0

    def test_half_hour_solar_excess_bucketed(self):
        """Solar excess is bucketed into half-hour intervals for persistence."""
        # Two snapshots in the 10:00 half-hour
        snapshots = [
            _make_full_snapshot(10, 0, feed_in_kw=2.0, pv_power_kw=3.0, load_power_kw=1.0),
            _make_full_snapshot(10, 5, feed_in_kw=2.0, pv_power_kw=3.0, load_power_kw=1.0),
        ]
        result = estimate_revenue(
            snapshots, [_make_tariff(10)], _thresholds(),
            datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        assert len(result.half_hour_solar_excess) == 1
        key = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc).isoformat()
        assert key in result.half_hour_solar_excess
        assert result.half_hour_solar_excess[key] == pytest.approx(0.1667, abs=0.001)
