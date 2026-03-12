"""Tests for the revenue calculator."""

from datetime import date, datetime, timezone

import pytest

from octopus_export_optimizer.calculations.revenue_calculator import RevenueCalculator
from octopus_export_optimizer.config.settings import FlatRateConfig, ThresholdSettings
from tests.factories import make_meter_interval, make_tariff_slot


@pytest.fixture
def calculator(thresholds):
    return RevenueCalculator(thresholds)


class TestCalculateInterval:
    def test_basic_revenue(self, calculator):
        meter = make_meter_interval(kwh=2.0)
        tariff = make_tariff_slot(
            interval_start=meter.interval_start, rate_pence=20.0
        )

        result = calculator.calculate_interval(meter, tariff)

        assert result.export_kwh == 2.0
        assert result.agile_rate_pence == 20.0
        assert result.agile_revenue_pence == 40.0  # 2.0 * 20.0
        assert result.flat_rate_pence == 12.0  # default
        assert result.flat_revenue_pence == 24.0  # 2.0 * 12.0
        assert result.uplift_pence == 16.0  # 40.0 - 24.0

    def test_negative_agile_rate(self, calculator):
        meter = make_meter_interval(kwh=1.0)
        tariff = make_tariff_slot(
            interval_start=meter.interval_start, rate_pence=-5.0
        )

        result = calculator.calculate_interval(meter, tariff)

        assert result.agile_revenue_pence == -5.0
        assert result.uplift_pence == -17.0  # -5.0 - 12.0

    def test_zero_export(self, calculator):
        meter = make_meter_interval(kwh=0.0)
        tariff = make_tariff_slot(
            interval_start=meter.interval_start, rate_pence=25.0
        )

        result = calculator.calculate_interval(meter, tariff)

        assert result.agile_revenue_pence == 0.0
        assert result.flat_revenue_pence == 0.0
        assert result.uplift_pence == 0.0

    def test_agile_below_flat(self, calculator):
        meter = make_meter_interval(kwh=1.0)
        tariff = make_tariff_slot(
            interval_start=meter.interval_start, rate_pence=5.0
        )

        result = calculator.calculate_interval(meter, tariff)

        assert result.uplift_pence == -7.0  # 5.0 - 12.0
        assert not result.is_uplift_positive


class TestDateEffectiveFlatRate:
    def test_different_flat_rate_for_date(self):
        thresholds = ThresholdSettings(
            flat_export_rates=[
                FlatRateConfig(rate_pence=10.0, effective_from=date(2024, 1, 1), effective_to=date(2025, 6, 30)),
                FlatRateConfig(rate_pence=15.0, effective_from=date(2025, 7, 1)),
            ]
        )
        calc = RevenueCalculator(thresholds)

        # Pre-July 2025: 10p
        meter = make_meter_interval(
            interval_start=datetime(2025, 3, 15, 12, 0, tzinfo=timezone.utc),
            kwh=1.0,
        )
        tariff = make_tariff_slot(
            interval_start=meter.interval_start, rate_pence=20.0
        )
        result = calc.calculate_interval(meter, tariff)
        assert result.flat_rate_pence == 10.0

        # Post-July 2025: 15p
        meter2 = make_meter_interval(
            interval_start=datetime(2025, 8, 1, 12, 0, tzinfo=timezone.utc),
            kwh=1.0,
        )
        tariff2 = make_tariff_slot(
            interval_start=meter2.interval_start, rate_pence=20.0
        )
        result2 = calc.calculate_interval(meter2, tariff2)
        assert result2.flat_rate_pence == 15.0


class TestCalculateBatch:
    def test_joins_by_interval_start(self, calculator):
        start1 = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        start2 = datetime(2026, 3, 9, 12, 30, tzinfo=timezone.utc)
        start3 = datetime(2026, 3, 9, 13, 0, tzinfo=timezone.utc)

        meters = [
            make_meter_interval(interval_start=start1, kwh=1.0),
            make_meter_interval(interval_start=start2, kwh=2.0),
            make_meter_interval(interval_start=start3, kwh=1.5),
        ]
        tariffs = [
            make_tariff_slot(interval_start=start1, rate_pence=10.0),
            make_tariff_slot(interval_start=start2, rate_pence=25.0),
            # No tariff for start3
        ]

        results = calculator.calculate_batch(meters, tariffs)

        assert len(results) == 2
        assert results[0].interval_start == start1
        assert results[1].interval_start == start2


class TestSolarExcessFlatBaseline:
    """Flat baseline uses stored solar excess when available."""

    def test_solar_excess_used_for_flat_baseline(self, calculator):
        """When solar excess is provided, flat uses it instead of meter kWh."""
        meter = make_meter_interval(kwh=5.0)  # Battery Force Discharge
        tariff = make_tariff_slot(
            interval_start=meter.interval_start, rate_pence=20.0
        )

        # Solar excess for this interval was only 1.0 kWh
        result = calculator.calculate_interval(
            meter, tariff, solar_excess_kwh=1.0,
        )

        assert result.export_kwh == 5.0  # Actual export unchanged
        assert result.agile_revenue_pence == 100.0  # 5 × 20p
        assert result.flat_export_kwh == 1.0  # Solar excess used for flat
        assert result.flat_revenue_pence == 12.0  # 1.0 × 12p (not 5 × 12p)
        assert result.uplift_pence == 88.0  # 100 - 12

    def test_no_solar_excess_falls_back_to_meter(self, calculator):
        """Without solar excess data, flat uses meter kWh (old behavior)."""
        meter = make_meter_interval(kwh=2.0)
        tariff = make_tariff_slot(
            interval_start=meter.interval_start, rate_pence=20.0
        )

        result = calculator.calculate_interval(meter, tariff)

        assert result.flat_export_kwh is None
        assert result.flat_revenue_pence == 24.0  # 2.0 × 12p

    def test_batch_with_solar_excess_map(self, calculator):
        """calculate_batch uses solar_excess_map for flat baseline."""
        start1 = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        start2 = datetime(2026, 3, 9, 12, 30, tzinfo=timezone.utc)

        meters = [
            make_meter_interval(interval_start=start1, kwh=3.0),
            make_meter_interval(interval_start=start2, kwh=4.0),
        ]
        tariffs = [
            make_tariff_slot(interval_start=start1, rate_pence=20.0),
            make_tariff_slot(interval_start=start2, rate_pence=20.0),
        ]
        solar_excess_map = {start1: 1.5}  # Only have data for first interval

        results = calculator.calculate_batch(
            meters, tariffs, solar_excess_map=solar_excess_map,
        )

        assert len(results) == 2
        # First interval: uses solar excess
        assert results[0].flat_export_kwh == 1.5
        assert results[0].flat_revenue_pence == 18.0  # 1.5 × 12p
        # Second interval: falls back to meter kWh
        assert results[1].flat_export_kwh is None
        assert results[1].flat_revenue_pence == 48.0  # 4.0 × 12p

    def test_zero_solar_excess_means_no_flat_export(self, calculator):
        """Zero solar excess (e.g., night) → flat revenue = 0."""
        meter = make_meter_interval(kwh=5.0)  # Force Discharge at night
        tariff = make_tariff_slot(
            interval_start=meter.interval_start, rate_pence=20.0
        )

        result = calculator.calculate_interval(
            meter, tariff, solar_excess_kwh=0.0,
        )

        assert result.flat_revenue_pence == 0.0
        assert result.uplift_pence == 100.0  # All Agile revenue is uplift


class TestCalculateImportCostBatch:
    def test_joins_by_interval_start(self, calculator):
        start1 = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        start2 = datetime(2026, 3, 9, 12, 30, tzinfo=timezone.utc)

        meters = [
            make_meter_interval(interval_start=start1, kwh=0.5, direction="import"),
            make_meter_interval(interval_start=start2, kwh=0.3, direction="import"),
        ]
        tariffs = [
            make_tariff_slot(interval_start=start1, rate_pence=7.5, tariff_type="import"),
        ]

        results = calculator.calculate_import_cost_batch(meters, tariffs)

        assert len(results) == 1
        assert results[0].import_kwh == 0.5
        assert results[0].import_rate_pence == 7.5
        assert results[0].import_cost_pence == pytest.approx(3.75)

    def test_empty_inputs(self, calculator):
        results = calculator.calculate_import_cost_batch([], [])
        assert results == []
