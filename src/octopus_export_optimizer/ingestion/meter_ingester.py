"""Meter interval data ingestion from Octopus Energy API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from octopus_export_optimizer.ingestion.octopus_client import OctopusClient
from octopus_export_optimizer.models.job import JobRun
from octopus_export_optimizer.models.meter import MeterInterval
from octopus_export_optimizer.storage.job_repo import JobRepo
from octopus_export_optimizer.storage.meter_repo import MeterRepo

logger = logging.getLogger(__name__)


class MeterIngester:
    """Fetches meter consumption data from the Octopus API and stores it."""

    def __init__(
        self,
        client: OctopusClient,
        meter_repo: MeterRepo,
        job_repo: JobRepo,
    ) -> None:
        self.client = client
        self.meter_repo = meter_repo
        self.job_repo = job_repo

    def ingest_export_data(self, lookback_hours: int = 72) -> JobRun:
        """Fetch and upsert export meter intervals.

        Meter data from Octopus is typically delayed by ~24 hours,
        so we look back further to catch newly available data.
        """
        return self._ingest(
            direction="export",
            fetch_fn=self.client.get_export_consumption,
            lookback_hours=lookback_hours,
        )

    def ingest_import_data(self, lookback_hours: int = 72) -> JobRun:
        """Fetch and upsert import meter intervals."""
        return self._ingest(
            direction="import",
            fetch_fn=self.client.get_import_consumption,
            lookback_hours=lookback_hours,
        )

    def _ingest(
        self,
        direction: str,
        fetch_fn: callable,
        lookback_hours: int,
    ) -> JobRun:
        now = datetime.now(timezone.utc)
        job = JobRun(
            job_type=f"ingest_{direction}_meter",
            started_at=now,
        )
        self.job_repo.save(job)

        try:
            period_from = now - timedelta(hours=lookback_hours)
            period_to = now

            raw_results = fetch_fn(period_from, period_to)
            intervals = self._parse_consumption(raw_results, direction, now)
            count = self.meter_repo.upsert_intervals(intervals)

            job.complete(records=count)
            logger.info(
                "Ingested %d %s meter intervals (lookback %dh)",
                count, direction, lookback_hours,
            )
        except Exception as e:
            job.fail(str(e))
            logger.error("Failed to ingest %s meter data: %s", direction, e)

        self.job_repo.save(job)
        return job

    @staticmethod
    def _parse_consumption(
        raw_results: list[dict],
        direction: str,
        fetched_at: datetime,
    ) -> list[MeterInterval]:
        """Parse raw API consumption response into MeterInterval models."""
        intervals = []
        for result in raw_results:
            interval_start = datetime.fromisoformat(
                result["interval_start"].replace("Z", "+00:00")
            )
            interval_end = datetime.fromisoformat(
                result["interval_end"].replace("Z", "+00:00")
            )
            intervals.append(
                MeterInterval(
                    interval_start=interval_start,
                    interval_end=interval_end,
                    kwh=result["consumption"],
                    direction=direction,
                    fetched_at=fetched_at,
                )
            )
        return intervals
