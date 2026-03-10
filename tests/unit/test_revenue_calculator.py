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
