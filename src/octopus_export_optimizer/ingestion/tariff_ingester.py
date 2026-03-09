"""Tariff data ingestion from Octopus Energy API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from octopus_export_optimizer.ingestion.octopus_client import OctopusClient
from octopus_export_optimizer.models.job import JobRun
from octopus_export_optimizer.models.tariff import TariffSlot
from octopus_export_optimizer.storage.job_repo import JobRepo
from octopus_export_optimizer.storage.tariff_repo import TariffRepo

logger = logging.getLogger(__name__)


class TariffIngester:
    """Fetches tariff rates from the Octopus API and stores them."""

    def __init__(
        self,
        client: OctopusClient,
        tariff_repo: TariffRepo,
        job_repo: JobRepo,
    ) -> None:
        self.client = client
        self.tariff_repo = tariff_repo
        self.job_repo = job_repo

    def ingest_export_rates(
        self,
        lookback_hours: int = 48,
        lookahead_hours: int = 48,
    ) -> JobRun:
        """Fetch and upsert export tariff rates."""
        return self._ingest(
            tariff_type="export",
            fetch_fn=self.client.get_export_rates,
            lookback_hours=lookback_hours,
            lookahead_hours=lookahead_hours,
        )

    def ingest_import_rates(
        self,
        lookback_hours: int = 48,
        lookahead_hours: int = 48,
    ) -> JobRun:
        """Fetch and upsert import tariff rates."""
        return self._ingest(
            tariff_type="import",
            fetch_fn=self.client.get_import_rates,
            lookback_hours=lookback_hours,
            lookahead_hours=lookahead_hours,
        )

    def _ingest(
        self,
        tariff_type: str,
        fetch_fn: callable,
        lookback_hours: int,
        lookahead_hours: int,
    ) -> JobRun:
        now = datetime.now(timezone.utc)
        job = JobRun(
            job_type=f"ingest_{tariff_type}_tariffs",
            started_at=now,
        )
        self.job_repo.save(job)

        try:
            period_from = now - timedelta(hours=lookback_hours)
            period_to = now + timedelta(hours=lookahead_hours)

            raw_results = fetch_fn(period_from, period_to)
            slots = self._parse_rates(raw_results, tariff_type, now, period_from, period_to)
            count = self.tariff_repo.upsert_slots(slots)

            job.complete(records=count)
            logger.info(
                "Ingested %d %s tariff slots (%s to %s)",
                count, tariff_type, period_from, period_to,
            )
        except Exception as e:
            job.fail(str(e))
            logger.error("Failed to ingest %s tariffs: %s", tariff_type, e)

        self.job_repo.save(job)
        return job

    def _parse_rates(
        self,
        raw_results: list[dict],
        tariff_type: str,
        fetched_at: datetime,
        period_from: datetime | None = None,
        period_to: datetime | None = None,
    ) -> list[TariffSlot]:
        """Parse raw API response into TariffSlot models.

        For flat-rate tariffs (where a single rate has no valid_to or spans
        a very long period), the rate is expanded into half-hour slots across
        the requested period so that revenue calculations can match them.
        """
        product_code = (
            self.client.settings.export_product_code
            if tariff_type == "export"
            else self.client.settings.import_product_code
        )

        slots = []
        for result in raw_results:
            valid_from = datetime.fromisoformat(
                result["valid_from"].replace("Z", "+00:00")
            )
            valid_to_raw = result.get("valid_to")
            if valid_to_raw:
                valid_to = datetime.fromisoformat(
                    valid_to_raw.replace("Z", "+00:00")
                )
            else:
                valid_to = None

            # Use value_inc_vat for revenue calculations
            rate = result.get("value_inc_vat", result.get("value_exc_vat", 0.0))

            # If valid_to is None or the slot spans more than 1 hour,
            # this is a flat-rate tariff — expand into half-hour slots
            if valid_to is None or (valid_to - valid_from) > timedelta(hours=1):
                expand_from = period_from if period_from else valid_from
                expand_to = period_to if period_to else (valid_to or fetched_at + timedelta(hours=48))
                # Align to half-hour boundaries
                expand_from = expand_from.replace(
                    minute=(expand_from.minute // 30) * 30, second=0, microsecond=0
                )
                cursor = expand_from
                while cursor < expand_to:
                    slot_end = cursor + timedelta(minutes=30)
                    provenance = "actual" if cursor < fetched_at else "published"
                    slots.append(
                        TariffSlot(
                            interval_start=cursor,
                            interval_end=slot_end,
                            rate_inc_vat_pence=rate,
                            tariff_type=tariff_type,
                            product_code=product_code,
                            provenance=provenance,
                            fetched_at=fetched_at,
                        )
                    )
                    cursor = slot_end
            else:
                provenance = "actual" if valid_from < fetched_at else "published"
                slots.append(
                    TariffSlot(
                        interval_start=valid_from,
                        interval_end=valid_to,
                        rate_inc_vat_pence=rate,
                        tariff_type=tariff_type,
                        product_code=product_code,
                        provenance=provenance,
                        fetched_at=fetched_at,
                    )
                )
        return slots
