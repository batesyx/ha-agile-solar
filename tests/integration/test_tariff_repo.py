"""Integration tests for tariff repository."""

from datetime import datetime, timedelta, timezone

import pytest

from octopus_export_optimizer.storage.tariff_repo import TariffRepo
from tests.factories import make_tariff_slot


@pytest.fixture
def repo(db):
    return TariffRepo(db)


class TestUpsertSlots:
    def test_insert_and_retrieve(self, repo):
        start = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        slot = make_tariff_slot(interval_start=start, rate_pence=22.5)

        count = repo.upsert_slots([slot])
        assert count == 1

        result = repo.get_current_export_rate(
            datetime(2026, 3, 9, 12, 15, tzinfo=timezone.utc)
        )
        assert result is not None
        assert result.rate_inc_vat_pence == 22.5

    def test_upsert_overwrites(self, repo):
        start = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        slot1 = make_tariff_slot(interval_start=start, rate_pence=10.0)
        slot2 = make_tariff_slot(interval_start=start, rate_pence=20.0)

        repo.upsert_slots([slot1])
        repo.upsert_slots([slot2])

        result = repo.get_current_export_rate(
            datetime(2026, 3, 9, 12, 15, tzinfo=timezone.utc)
        )
        assert result.rate_inc_vat_pence == 20.0

    def test_empty_list(self, repo):
        assert repo.upsert_slots([]) == 0


class TestGetExportRates:
    def test_range_query(self, repo):
        base = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
        slots = [
            make_tariff_slot(interval_start=base + timedelta(minutes=30 * i), rate_pence=10.0 + i)
            for i in range(6)
        ]
        repo.upsert_slots(slots)

        results = repo.get_export_rates(
            datetime(2026, 3, 9, 11, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 9, 12, 30, tzinfo=timezone.utc),
        )
        assert len(results) == 3  # 11:00, 11:30, 12:00


class TestGetUpcomingExportRates:
    def test_lookahead(self, repo):
        now = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        slots = [
            make_tariff_slot(
                interval_start=now + timedelta(minutes=30 * i),
                rate_pence=10.0 + i,
            )
            for i in range(10)
        ]
        repo.upsert_slots(slots)

        results = repo.get_upcoming_export_rates(now, hours=2.0)
        assert len(results) == 4  # 2 hours = 4 half-hour slots
