"""Inverter controller — sends work mode and Max SoC commands via HA REST API."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx

from octopus_export_optimizer.config.settings import HaSettings, InverterControlSettings
from octopus_export_optimizer.control.mode_mapper import WorkMode, map_recommendation_to_mode
from octopus_export_optimizer.control.models import CommandResult
from octopus_export_optimizer.models.recommendation import Recommendation
from octopus_export_optimizer.recommendation.types import RecommendationState
from octopus_export_optimizer.storage.command_repo import CommandRepo

logger = logging.getLogger(__name__)


class InverterController:
    """Controls the Fox ESS inverter via Home Assistant REST API.

    Safety layers:
    1. Config `enabled` must be True.
    2. MQTT kill switch (`auto_control_enabled`) must be True — default OFF.
    3. INSUFFICIENT_DATA recommendations are never acted on.
    4. Rate-limited to one command per `min_command_interval_seconds`.
    5. Only sends commands when target differs from last commanded mode.
    """

    def __init__(
        self,
        ha_settings: HaSettings,
        inverter_settings: InverterControlSettings,
        command_repo: CommandRepo,
    ) -> None:
        self.ha_settings = ha_settings
        self.settings = inverter_settings
        self.command_repo = command_repo

        self._auto_control_enabled: bool = False
        self._extra_buffer_kwh: float = 0.0
        self._last_commanded_mode: WorkMode | None = None
        self._last_commanded_max_soc: int | None = None
        self._last_commanded_charge_kw: float | None = None
        self._last_commanded_discharge_kw: float | None = None
        self._last_command_time: datetime | None = None

        self._client = httpx.Client(
            base_url=ha_settings.url,
            headers={
                "Authorization": f"Bearer {ha_settings.token.get_secret_value()}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )

    @property
    def auto_control_enabled(self) -> bool:
        return self._auto_control_enabled

    @property
    def extra_buffer_kwh(self) -> float:
        return self._extra_buffer_kwh

    @property
    def last_commanded_mode(self) -> WorkMode | None:
        return self._last_commanded_mode

    def set_auto_control(self, enabled: bool) -> None:
        """Set by MQTT kill switch callback."""
        self._auto_control_enabled = enabled
        logger.info("Auto control %s", "enabled" if enabled else "disabled")

    def set_extra_buffer(self, kwh: float) -> None:
        """Set by MQTT buffer slider callback."""
        self._extra_buffer_kwh = max(0.0, min(10.0, kwh))
        logger.info("Extra buffer set to %.1f kWh", self._extra_buffer_kwh)

    def execute(self, recommendation: Recommendation) -> CommandResult | None:
        """Execute inverter control based on a recommendation.

        Returns a CommandResult if a command was sent, None otherwise.
        """
        # Safety: config must enable control
        if not self.settings.enabled:
            logger.debug("Skipping control: inverter control disabled in config")
            return None

        # Safety: MQTT kill switch must be ON
        if not self._auto_control_enabled:
            logger.info("Skipping control: auto control switch is OFF")
            return None

        # Handle insufficient data: fallback to safe mode or no-op
        if recommendation.state == RecommendationState.INSUFFICIENT_DATA:
            if self.settings.fallback_on_insufficient_data == "feed_in_first":
                target_mode = WorkMode.FEED_IN_FIRST
                logger.info(
                    "Insufficient data — falling back to Feed-in First (safe mode)"
                )
            else:
                return None
        else:
            # Map recommendation to target work mode
            target_mode = map_recommendation_to_mode(
                RecommendationState(recommendation.state)
            )
            if target_mode is None:
                return None

        # Override work mode if the engine set an explicit override
        # (e.g., Self Use during solar charging windows)
        if recommendation.target_work_mode_override is not None:
            target_mode = WorkMode(recommendation.target_work_mode_override)

        # Override to Force Discharge when export planner sets a power target
        target_discharge_kw = recommendation.target_discharge_kw
        if target_discharge_kw is not None:
            target_mode = WorkMode.FORCE_DISCHARGE

        target_charge_kw = recommendation.target_charge_kw
        target_max_soc = recommendation.target_max_soc
        if target_max_soc is not None:
            target_max_soc = max(10, min(100, target_max_soc))

        # Check if anything actually needs to change
        mode_changed = target_mode != self._last_commanded_mode
        soc_changed = (
            target_max_soc is not None
            and target_max_soc != self._last_commanded_max_soc
        )
        charge_kw_changed = (
            target_charge_kw is not None
            and target_charge_kw != self._last_commanded_charge_kw
        )
        discharge_kw_changed = (
            target_discharge_kw is not None
            and target_discharge_kw != self._last_commanded_discharge_kw
        )
        if not mode_changed and not soc_changed and not charge_kw_changed and not discharge_kw_changed:
            logger.debug(
                "No change needed: mode=%s, max_soc=%s, charge_kw=%s, discharge_kw=%s",
                target_mode.value, target_max_soc, target_charge_kw, target_discharge_kw,
            )
            return None  # Idempotent — no-op

        # Rate limit
        now = datetime.now(timezone.utc)
        if self._last_command_time is not None:
            elapsed = (now - self._last_command_time).total_seconds()
            if elapsed < self.settings.min_command_interval_seconds:
                logger.debug(
                    "Rate limited: %ds since last command (min %ds)",
                    int(elapsed),
                    self.settings.min_command_interval_seconds,
                )
                return None

        # Execute commands
        previous_mode = self._last_commanded_mode.value if self._last_commanded_mode else None
        success = True
        error = None

        try:
            if soc_changed and target_max_soc is not None:
                self._send_max_soc(target_max_soc)

            if charge_kw_changed and target_charge_kw is not None:
                self._send_force_charge_power(target_charge_kw)

            if mode_changed:
                self._send_work_mode(target_mode)

            if discharge_kw_changed and target_discharge_kw is not None:
                self._send_force_discharge_power(target_discharge_kw)
        except Exception as e:
            success = False
            error = str(e)
            logger.error("Inverter command failed: %s", e)

        # Record result
        result = CommandResult(
            id=uuid.uuid4().hex,
            timestamp=now,
            previous_mode=previous_mode,
            new_mode=target_mode.value,
            target_max_soc=target_max_soc,
            target_discharge_kw=target_discharge_kw,
            recommendation_state=recommendation.state,
            reason_code=recommendation.reason_code,
            success=success,
            error=error,
        )
        self.command_repo.save(result)

        if success:
            self._last_commanded_mode = target_mode
            self._last_commanded_max_soc = target_max_soc
            self._last_commanded_charge_kw = target_charge_kw
            self._last_commanded_discharge_kw = target_discharge_kw
            self._last_command_time = now
            logger.info(
                "Inverter command: %s → %s (max_soc=%s, charge_kw=%s, discharge_kw=%s, reason=%s)",
                previous_mode,
                target_mode.value,
                target_max_soc,
                target_charge_kw,
                target_discharge_kw,
                recommendation.reason_code,
            )

        return result

    def _send_work_mode(self, mode: WorkMode) -> None:
        """Set inverter work mode via HA select entity."""
        resp = self._client.post(
            "/api/services/select/select_option",
            json={
                "entity_id": self.ha_settings.entity_ids.work_mode,
                "option": mode.value,
            },
        )
        resp.raise_for_status()
        logger.debug("Set work mode to %s", mode.value)

    def _send_force_charge_power(self, power_kw: float) -> None:
        """Set force charge power via HA number entity."""
        resp = self._client.post(
            "/api/services/number/set_value",
            json={
                "entity_id": self.ha_settings.entity_ids.force_charge_power,
                "value": round(power_kw, 3),
            },
        )
        resp.raise_for_status()
        logger.debug("Set force charge power to %.3f kW", power_kw)

    def _send_force_discharge_power(self, power_kw: float) -> None:
        """Set force discharge power via HA number entity."""
        resp = self._client.post(
            "/api/services/number/set_value",
            json={
                "entity_id": self.ha_settings.entity_ids.force_discharge_power,
                "value": round(power_kw, 3),
            },
        )
        resp.raise_for_status()
        logger.debug("Set force discharge power to %.3f kW", power_kw)

    def _send_max_soc(self, value: int) -> None:
        """Set Max SoC via HA number entity."""
        resp = self._client.post(
            "/api/services/number/set_value",
            json={
                "entity_id": self.ha_settings.entity_ids.max_soc,
                "value": value,
            },
        )
        resp.raise_for_status()
        logger.debug("Set max SoC to %d%%", value)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
