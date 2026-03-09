"""MQTT publisher for Home Assistant integration.

Publishes state data via MQTT with HA discovery messages
so entities are automatically created in Home Assistant.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import paho.mqtt.client as mqtt

from octopus_export_optimizer.config.settings import MqttSettings
from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.models.recommendation import Recommendation
from octopus_export_optimizer.models.revenue import RevenueSummary
from octopus_export_optimizer.models.tariff import TariffSlot
from octopus_export_optimizer.publishing.payload_builder import PayloadBuilder

logger = logging.getLogger(__name__)

DEVICE_INFO = {
    "identifiers": ["octopus_export_optimizer"],
    "name": "Octopus Export Optimizer",
    "manufacturer": "Custom",
    "model": "Export Optimizer v0.1",
}


class MqttPublisher:
    """Publishes optimizer state to Home Assistant via MQTT."""

    def __init__(self, settings: MqttSettings) -> None:
        self.settings = settings
        self.prefix = settings.topic_prefix
        self.discovery_prefix = settings.ha_discovery_prefix
        self.builder = PayloadBuilder()
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="octopus_export_optimizer",
        )
        if settings.username:
            self._client.username_pw_set(
                settings.username,
                settings.password.get_secret_value() if settings.password else None,
            )

    def connect(self) -> None:
        """Connect to the MQTT broker and publish discovery."""
        self._client.will_set(
            f"{self.prefix}/service/status", "offline", retain=True
        )
        self._client.connect(self.settings.broker, self.settings.port)
        self._client.loop_start()
        self._publish_discovery()
        self._publish(f"{self.prefix}/service/status", "online", retain=True)
        logger.info("MQTT connected to %s:%d", self.settings.broker, self.settings.port)

    def disconnect(self) -> None:
        """Publish offline status and disconnect."""
        self._publish(f"{self.prefix}/service/status", "offline", retain=True)
        self._client.loop_stop()
        self._client.disconnect()

    def publish_rates(
        self,
        current_export: TariffSlot | None,
        best_upcoming: TariffSlot | None,
        current_import: TariffSlot | None = None,
    ) -> None:
        """Publish current and upcoming rate data."""
        self._publish(
            f"{self.prefix}/rates/export/current",
            self.builder.rate_payload(current_export),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/rates/export/best_upcoming",
            self.builder.best_upcoming_payload(best_upcoming),
            retain=True,
        )
        if current_import:
            self._publish(
                f"{self.prefix}/rates/import/current",
                self.builder.rate_payload(current_import),
                retain=True,
            )

    def publish_recommendation(self, rec: Recommendation | None) -> None:
        """Publish recommendation state."""
        self._publish(
            f"{self.prefix}/recommendation/state",
            self.builder.recommendation_state_payload(rec),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/recommendation/explanation",
            self.builder.recommendation_explanation_payload(rec),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/recommendation/reason_code",
            self.builder.recommendation_reason_payload(rec),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/recommendation/mode",
            self.builder.recommendation_mode_payload(rec),
            retain=True,
        )

    def publish_revenue(
        self,
        today: RevenueSummary | None,
        month: RevenueSummary | None,
    ) -> None:
        """Publish today and month revenue summaries."""
        for period, summary in [("today", today), ("month", month)]:
            payloads = self.builder.revenue_payload(summary)
            for key, value in payloads.items():
                self._publish(
                    f"{self.prefix}/revenue/{period}/{key}",
                    value,
                    retain=True,
                )

    def publish_ha_state(self, snapshot: HaStateSnapshot | None) -> None:
        """Publish current HA state values."""
        if snapshot is None:
            return
        self._publish(
            f"{self.prefix}/battery/soc",
            self.builder.float_payload(snapshot.battery_soc_pct, 1),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/solar/pv_power",
            self.builder.float_payload(snapshot.pv_power_kw),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/solar/feed_in",
            self.builder.float_payload(snapshot.feed_in_kw),
            retain=True,
        )

    def publish_service_status(self, last_run: datetime | None) -> None:
        """Publish service health status."""
        self._publish(
            f"{self.prefix}/service/status", "online", retain=True
        )
        self._publish(
            f"{self.prefix}/service/last_run",
            self.builder.timestamp_payload(last_run),
            retain=True,
        )

    def _publish(self, topic: str, payload: str, retain: bool = False) -> None:
        """Publish a message to MQTT."""
        self._client.publish(topic, payload, retain=retain)

    def _publish_discovery(self) -> None:
        """Publish HA MQTT discovery config for all entities."""
        sensors = [
            ("export_rate", "rates/export/current", "Export Rate", "p/kWh", "mdi:currency-gbp"),
            ("best_upcoming_rate", "rates/export/best_upcoming", "Best Upcoming Export Rate", "p/kWh", "mdi:chart-line"),
            ("recommendation_state", "recommendation/state", "Recommendation", None, "mdi:lightbulb-on"),
            ("recommendation_explanation", "recommendation/explanation", "Recommendation Detail", None, "mdi:text"),
            ("recommendation_reason", "recommendation/reason_code", "Recommendation Reason", None, "mdi:tag"),
            ("recommendation_mode", "recommendation/mode", "Recommendation Mode", None, "mdi:tune"),
            ("today_actual_revenue", "revenue/today/actual_pence", "Today Export Revenue", "p", "mdi:cash"),
            ("today_flat_revenue", "revenue/today/flat_pence", "Today Flat Baseline Revenue", "p", "mdi:cash-minus"),
            ("today_uplift", "revenue/today/uplift_pence", "Today Uplift vs Flat", "p", "mdi:cash-plus"),
            ("today_export_kwh", "revenue/today/export_kwh", "Today Exported", "kWh", "mdi:lightning-bolt"),
            ("month_actual_revenue", "revenue/month/actual_pence", "Month Export Revenue", "p", "mdi:cash"),
            ("month_flat_revenue", "revenue/month/flat_pence", "Month Flat Baseline Revenue", "p", "mdi:cash-minus"),
            ("month_uplift", "revenue/month/uplift_pence", "Month Uplift vs Flat", "p", "mdi:cash-plus"),
            ("month_export_kwh", "revenue/month/export_kwh", "Month Exported", "kWh", "mdi:lightning-bolt"),
            ("battery_soc", "battery/soc", "Optimizer Battery SoC", "%", "mdi:battery"),
            ("pv_power", "solar/pv_power", "Optimizer PV Power", "kW", "mdi:solar-power"),
            ("feed_in", "solar/feed_in", "Optimizer Feed-in", "kW", "mdi:transmission-tower-export"),
            ("last_run", "service/last_run", "Optimizer Last Run", None, "mdi:clock-outline"),
        ]

        for object_id, state_suffix, name, unit, icon in sensors:
            config = {
                "name": name,
                "state_topic": f"{self.prefix}/{state_suffix}",
                "unique_id": f"octopus_export_optimizer_{object_id}",
                "device": DEVICE_INFO,
                "icon": icon,
            }
            if unit:
                config["unit_of_measurement"] = unit
            topic = (
                f"{self.discovery_prefix}/sensor"
                f"/octopus_export_optimizer/{object_id}/config"
            )
            self._publish(topic, json.dumps(config), retain=True)

        # Binary sensor for service status
        status_config = {
            "name": "Optimizer Status",
            "state_topic": f"{self.prefix}/service/status",
            "unique_id": "octopus_export_optimizer_status",
            "device": DEVICE_INFO,
            "payload_on": "online",
            "payload_off": "offline",
            "device_class": "connectivity",
            "icon": "mdi:heart-pulse",
        }
        topic = (
            f"{self.discovery_prefix}/binary_sensor"
            f"/octopus_export_optimizer/status/config"
        )
        self._publish(topic, json.dumps(status_config), retain=True)

    def __enter__(self) -> MqttPublisher:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.disconnect()
