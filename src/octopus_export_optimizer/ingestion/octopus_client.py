"""HTTP client for the Octopus Energy REST API."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from octopus_export_optimizer.config.constants import OCTOPUS_API_BASE
from octopus_export_optimizer.config.settings import OctopusApiSettings

logger = logging.getLogger(__name__)


class OctopusClient:
    """Thin wrapper around the Octopus Energy v1 API.

    Authentication is HTTP Basic with the API key as username
    and an empty password.
    """

    def __init__(self, settings: OctopusApiSettings) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=OCTOPUS_API_BASE,
            auth=(settings.api_key.get_secret_value(), ""),
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def get_export_rates(
        self,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """Fetch export tariff rates for a date range."""
        return self._get_rates(
            product_code=self.settings.export_product_code,
            tariff_code=self.settings.export_tariff_code,
            period_from=period_from,
            period_to=period_to,
        )

    def get_import_rates(
        self,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """Fetch import tariff rates for a date range."""
        return self._get_rates(
            product_code=self.settings.import_product_code,
            tariff_code=self.settings.import_tariff_code,
            period_from=period_from,
            period_to=period_to,
        )

    def get_meter_consumption(
        self,
        mpan: str,
        serial: str,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """Fetch half-hour meter consumption data."""
        url = f"/electricity-meter-points/{mpan}/meters/{serial}/consumption/"
        return self._paginated_get(
            url,
            params={
                "period_from": period_from.isoformat(),
                "period_to": period_to.isoformat(),
                "order_by": "period",
                "page_size": 25000,
            },
        )

    def get_export_consumption(
        self,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """Fetch export meter consumption using configured MPAN/serial."""
        return self.get_meter_consumption(
            mpan=self.settings.export_mpan,
            serial=self.settings.export_serial,
            period_from=period_from,
            period_to=period_to,
        )

    def get_import_consumption(
        self,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """Fetch import meter consumption using configured MPAN/serial."""
        if not self.settings.import_mpan or not self.settings.import_serial:
            return []
        return self.get_meter_consumption(
            mpan=self.settings.import_mpan,
            serial=self.settings.import_serial,
            period_from=period_from,
            period_to=period_to,
        )

    def _get_rates(
        self,
        product_code: str,
        tariff_code: str,
        period_from: datetime,
        period_to: datetime,
    ) -> list[dict]:
        """Fetch tariff rates with pagination."""
        url = (
            f"/products/{product_code}"
            f"/electricity-tariffs/{tariff_code}"
            f"/standard-unit-rates/"
        )
        return self._paginated_get(
            url,
            params={
                "period_from": period_from.isoformat(),
                "period_to": period_to.isoformat(),
                "page_size": 1500,
            },
        )

    def _paginated_get(self, url: str, params: dict) -> list[dict]:
        """Fetch all pages of a paginated API response."""
        all_results: list[dict] = []
        next_url: str | None = url

        while next_url:
            response = self._client.get(next_url, params=params if next_url == url else None)
            response.raise_for_status()
            data = response.json()
            all_results.extend(data.get("results", []))
            next_url = data.get("next")
            if next_url:
                # next_url is a full URL; extract the path
                next_url = next_url.replace(OCTOPUS_API_BASE, "")

        logger.debug("Fetched %d results from %s", len(all_results), url)
        return all_results

    def __enter__(self) -> OctopusClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
