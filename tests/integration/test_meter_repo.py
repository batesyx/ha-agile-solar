"""Integration tests for meter repository."""

from datetime import datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.storage.meter_repo import MeterRepo
from tests.factories import make_meter_interval


@pytest.fixture
def repo(db):
    return MeterRepo(db)


class TestUpsertIntervals:
    def test_insert_and_retrieve(self, repo):
        start = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        interval = make_meter_interval(interval_start=start, kwh=2.5)

        count = repo.upsert_intervals([interval])
        assert count == 1

        results = repo.get_export_intervals(
            datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc),
        )
        assert len(results) == 1
        assert results[0].kwh == 2.5

    def test_idempotent_upsert(self, repo):
        start = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        interval = make_meter_interval(interval_start=start, kwh=2.5)

        repo.upsert_intervals([interval])
        repo.upsert_intervals([interval])

        results = repo.get_export_intervals(
            datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc),
        )
        assert len(results) == 1


class TestGetLatest:
    def test_returns_most_recent(self, repo):
        base = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
        intervals = [
            make_meter_interval(interval_start=base + timedelta(minutes=30 * i), kwh=float(i))
            for i in range(5)
        ]
        repo.upsert_intervals(intervals)

        latest = repo.get_latest_export_interval()
        assert latest is not None
        assert latest.kwh == 4.0
