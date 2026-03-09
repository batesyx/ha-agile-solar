"""Integration tests for revenue repository."""

from datetime import datetime, timezone

import pytest

from octopus_export_optimizer.models.revenue import RevenueInterval, RevenueSummary
from octopus_export_optimizer.storage.revenue_repo import RevenueRepo


@pytest.fixture
def repo(db):
    return RevenueRepo(db)


class TestRevenueIntervals:
    def test_upsert_and_retrieve(self, repo):
        now = datetime.now(timezone.utc)
        interval = RevenueInterval(
            interval_start=datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc),
            export_kwh=1.5,
            agile_rate_pence=20.0,
            agile_revenue_pence=30.0,
            flat_rate_pence=12.0,
            flat_revenue_pence=18.0,
            uplift_pence=12.0,
            calculated_at=now,
        )
        repo.upsert_intervals([interval])

        results = repo.get_intervals(
            datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc),
        )
        assert len(results) == 1
        assert results[0].agile_revenue_pence == 30.0


class TestRevenueSummaries:
    def test_upsert_and_get_summary(self, repo):
        now = datetime.now(timezone.utc)
        summary = RevenueSummary(
            period_type="day",
            period_key="2026-03-09",
            total_export_kwh=10.5,
            agile_revenue_pence=200.0,
            flat_revenue_pence=126.0,
            uplift_pence=74.0,
            avg_realised_rate_pence=19.05,
            intervals_above_flat=15,
            total_intervals=20,
            calculated_at=now,
        )
        repo.upsert_summary(summary)

        result = repo.get_summary("day", "2026-03-09")
        assert result is not None
        assert result.total_export_kwh == 10.5
        assert result.uplift_pence == 74.0
