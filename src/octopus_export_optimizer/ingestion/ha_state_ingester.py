"""Home Assistant state ingestion via REST API polling."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from octopus_export_optimizer.config.settings import HaSettings
from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.storage.ha_state_repo import HaStateRepo

logger = logging.getLogger(__name__)


class HaStateIngester:
    """Polls Home Assistant REST API for current device state."""

    def __init__(
        self,
        settings: HaSettings,
        ha_state_repo: HaStateRepo,
    ) -> None:
        self.settings = settings
        self.ha_state_repo = ha_state_repo
        self._client = httpx.Client(
            base_url=settings.url,
            headers={
                "Authorization": f"Bearer {settings.token.get_secret_value()}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )

    def close(self) -> None:
        self._client.close()

    def poll(self) -> HaStateSnapshot | None:
        """Poll HA for current state and store a snapshot.

        Returns the snapshot on success, None on failure.
        """
        try:
            entities = self.settings.entity_ids
            now = datetime.now(timezone.utc)

            snapshot = HaStateSnapshot(
                timestamp=now,
                battery_soc_pct=self._get_float(entities.battery_soc),
                pv_power_kw=self._get_power_kw(entities.pv_power),
                feed_in_kw=self._get_power_kw(entities.feed_in_power),
                load_power_kw=self._get_power_kw(entities.load_power),
                grid_consumption_kw=self._get_power_kw(entities.grid_consumption),
                battery_charge_kw=self._get_power_kw(entities.battery_charge_power),
                battery_discharge_kw=self._get_power_kw(entities.battery_discharge_power),
                work_mode=self._get_state(entities.work_mode),
                max_soc=self._get_float(entities.max_soc),
                min_soc=self._get_float(entities.min_soc),
                force_charge_power_kw=self._get_power_kw(entities.force_charge_power),
                force_discharge_power_kw=self._get_power_kw(entities.force_discharge_power),
            )

            self.ha_state_repo.insert(snapshot)
            logger.debug("HA state snapshot saved: SoC=%.1f%%", snapshot.battery_soc_pct or 0)
            return snapshot

        except Exception as e:
            logger.error("Failed to poll HA state: %s", e)
            return None

    def _get_state(self, entity_id: str) -> str | None:
        """Get the raw state string for an entity."""
        try:
            response = self._client.get(f"/api/states/{entity_id}")
            response.raise_for_status()
            data = response.json()
            state = data.get("state")
            if state in ("unavailable", "unknown"):
                return None
            return state
        except Exception:
            logger.debug("Could not fetch state for %s", entity_id)
            return None

    def _get_float(self, entity_id: str) -> float | None:
        """Get a numeric state value as float."""
        state = self._get_state(entity_id)
        if state is None:
            return None
        try:
            return float(state)
        except (ValueError, TypeError):
            return None

    def _get_power_kw(self, entity_id: str) -> float | None:
        """Get a power value and convert to kW.

        Fox ESS sensors typically report in watts or kW depending on
        the integration. This attempts to detect and normalise.
        """
        value = self._get_float(entity_id)
        if value is None:
            return None
        # If value > 100, assume it's in watts and convert to kW
        if abs(value) > 100:
            return value / 1000.0
        return value

    def __enter__(self) -> HaStateIngester:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
