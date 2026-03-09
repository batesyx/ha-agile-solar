"""Tests for the aggregator."""

from datetime import date, datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.calculations.aggregator import Aggregator
from octopus_export_optimizer.models.revenue import RevenueInterval


@pytest.fixture
def aggregator():
    return Aggregator()


def make_revenue_interval(start_hour: int, kwh: float, agile_rate: float) -> RevenueInterval:
    start = datetime(2026, 3, 9, start_hour, 0, tzinfo=timezone.utc)
    return RevenueInterval(
        interval_start=start,
        export_kwh=kwh,
        agile_rate_pence=agile_rate,
        agile_revenue_pence=kwh * agile_rate,
        flat_rate_pence=12.0,
        flat_revenue_pence=kwh * 12.0,
        uplift_pence=(kwh * agile_rate) - (kwh * 12.0),
        calculated_at=datetime.now(timezone.utc),
    )


class TestAggregate:
    def test_empty_intervals(self, aggregator):
        summary = aggregator.aggregate([], "day", "2026-03-09")
        assert summary.total_export_kwh == 0.0
        assert summary.total_intervals == 0

    def test_sums_correctly(self, aggregator):
        intervals = [
            make_revenue_interval(12, 1.5, 20.0),
            make_revenue_interval(13, 2.0, 15.0),
            make_revenue_interval(14, 1.0, 5.0),
        ]
        summary = aggregator.aggregate(intervals, "day", "2026-03-09")

        assert summary.total_export_kwh == pytest.approx(4.5)
        assert summary.agile_revenue_pence == pytest.approx(65.0)  # 30+30+5
        assert summary.flat_revenue_pence == pytest.approx(54.0)  # 4.5 * 12
        assert summary.uplift_pence == pytest.approx(11.0)
        assert summary.intervals_above_flat == 2  # 20p and 15p above 12p
        assert summary.total_intervals == 3

    def test_avg_realised_rate(self, aggregator):
        intervals = [
            make_revenue_interval(12, 2.0, 20.0),  # 40p
            make_revenue_interval(13, 2.0, 10.0),  # 20p
        ]
        summary = aggregator.aggregate(intervals, "day", "2026-03-09")
        # Total revenue 60p / 4 kWh = 15 p/kWh
        assert summary.avg_realised_rate_pence == pytest.approx(15.0)


class TestDayBoundaries:
    def test_utc_boundaries_for_gmt(self, aggregator):
        # In winter (GMT), midnight London = midnight UTC
        start, end = aggregator.day_boundaries(date(2026, 1, 15))
        assert start.hour == 0
        assert end.hour == 0

    def test_utc_boundaries_for_bst(self, aggregator):
        # In summer (BST), midnight London = 23:00 UTC previous day
        start, end = aggregator.day_boundaries(date(2026, 7, 15))
        assert start.hour == 23
