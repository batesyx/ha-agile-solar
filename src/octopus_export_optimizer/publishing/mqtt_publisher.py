"""MQTT publisher for Home Assistant integration.

Publishes state data via MQTT with HA discovery messages
so entities are automatically created in Home Assistant.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import paho.mqtt.client as mqtt

from typing import Callable

from octopus_export_optimizer.config.settings import MqttSettings
from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.models.export_plan import ExportPlan
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
            f"{self.prefix}/rates/export/current_slot",
            (
                f"{current_export.interval_start.strftime('%H:%M')} – {current_export.interval_end.strftime('%H:%M')}"
                if current_export else ""
            ),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/rates/export/best_upcoming",
            self.builder.best_upcoming_payload(best_upcoming),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/rates/export/best_upcoming_time",
            best_upcoming.interval_start.strftime("%H:%M") if best_upcoming else "",
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

        # Export plan fields
        if rec and rec.target_discharge_kw is not None:
            self._publish(
                f"{self.prefix}/plan/discharge_kw",
                f"{rec.target_discharge_kw:.1f}",
                retain=True,
            )
            self._publish(
                f"{self.prefix}/plan/status",
                "ACTIVE",
                retain=True,
            )
        elif rec and rec.reason_code.value == "PLANNED_HOLD":
            self._publish(
                f"{self.prefix}/plan/discharge_kw", "0.0", retain=True,
            )
            self._publish(
                f"{self.prefix}/plan/status", "HOLDING", retain=True,
            )
        else:
            self._publish(
                f"{self.prefix}/plan/discharge_kw", "0.0", retain=True,
            )
            self._publish(
                f"{self.prefix}/plan/status", "NONE", retain=True,
            )
        self._publish(
            f"{self.prefix}/plan/slots_planned",
            str(rec.export_plan_slots or 0) if rec else "0",
            retain=True,
        )

    def publish_export_plan(
        self,
        rec: Recommendation | None,
        plan: ExportPlan | None,
        now: datetime,
    ) -> None:
        """Publish battery plan detail sensors."""
        # Target max SoC from recommendation
        target_soc = (
            str(rec.target_max_soc)
            if rec and rec.target_max_soc is not None
            else ""
        )
        self._publish(f"{self.prefix}/control/target_max_soc", target_soc, retain=True)

        # Next planned discharge slot
        next_slot = plan.get_next_slot(now) if plan else None
        self._publish(
            f"{self.prefix}/plan/next_slot_time",
            next_slot.interval_start.isoformat() if next_slot else "",
            retain=True,
        )
        self._publish(
            f"{self.prefix}/plan/next_slot_rate",
            f"{next_slot.rate_pence:.1f}" if next_slot else "",
            retain=True,
        )

    def publish_revenue(
        self,
        today: RevenueSummary | None,
        month: RevenueSummary | None,
        today_is_estimated: bool = False,
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
        # Publish whether today's figures are estimated or settled
        self._publish(
            f"{self.prefix}/revenue/today/source",
            "estimated" if today_is_estimated else "settled",
            retain=True,
        )

    def publish_rolling_revenue(
        self,
        rolling_7d: RevenueSummary | None,
        rolling_30d: RevenueSummary | None,
    ) -> None:
        """Publish rolling 7-day and 30-day revenue summaries."""
        for period, summary in [("7d", rolling_7d), ("30d", rolling_30d)]:
            payloads = self.builder.revenue_payload(summary)
            for key, value in payloads.items():
                self._publish(
                    f"{self.prefix}/revenue/{period}/{key}",
                    value,
                    retain=True,
                )
            # Also publish average rate
            if summary and summary.total_export_kwh > 0:
                self._publish(
                    f"{self.prefix}/revenue/{period}/avg_rate",
                    f"{summary.avg_realised_rate_pence:.1f}",
                    retain=True,
                )
            else:
                self._publish(
                    f"{self.prefix}/revenue/{period}/avg_rate",
                    "0.0",
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

    def _publish_schedule(
        self,
        topic_base: str,
        slots: list[TariffSlot],
        current_time: datetime | None,
        planned_starts: set[str] | None = None,
    ) -> None:
        """Publish a rate schedule: JSON to attributes topic, count to state topic."""
        payload = self.builder.rate_schedule_payload(
            slots, current_time, planned_starts=planned_starts,
        )
        self._publish(f"{topic_base}", payload, retain=True)
        # Short state value for HA (avoids 255-char limit)
        parsed = json.loads(payload)
        self._publish(f"{topic_base}/state", str(parsed.get("count", 0)), retain=True)

    def publish_rate_schedule(
        self,
        export_slots: list[TariffSlot],
        import_slots: list[TariffSlot] | None = None,
        current_time: datetime | None = None,
    ) -> None:
        """Publish today's full rate schedule for charting."""
        self._publish_schedule(
            f"{self.prefix}/rates/export/schedule", export_slots, current_time,
        )
        if import_slots:
            self._publish_schedule(
                f"{self.prefix}/rates/import/schedule", import_slots, current_time,
            )

    def publish_upcoming_rate_schedule(
        self,
        export_slots: list[TariffSlot],
        import_slots: list[TariffSlot] | None = None,
        current_time: datetime | None = None,
        planned_starts: set[str] | None = None,
    ) -> None:
        """Publish forward-looking rate schedule (now → 48h) for charting."""
        self._publish_schedule(
            f"{self.prefix}/rates/export/upcoming_schedule",
            export_slots, current_time, planned_starts=planned_starts,
        )
        if import_slots:
            self._publish_schedule(
                f"{self.prefix}/rates/import/upcoming_schedule",
                import_slots, current_time,
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

    def publish_data_freshness(
        self,
        tariff_age_minutes: float | None,
        ha_state_age_minutes: float | None,
        freshness_limit: float,
    ) -> None:
        """Publish data freshness metrics for health monitoring."""
        def _status(age: float | None) -> str:
            if age is None:
                return "UNAVAILABLE"
            return "OK" if age <= freshness_limit else "STALE"

        self._publish(
            f"{self.prefix}/health/tariff_age",
            f"{tariff_age_minutes:.0f}" if tariff_age_minutes is not None else "",
            retain=True,
        )
        self._publish(
            f"{self.prefix}/health/tariff_status",
            _status(tariff_age_minutes),
            retain=True,
        )
        self._publish(
            f"{self.prefix}/health/ha_state_age",
            f"{ha_state_age_minutes:.0f}" if ha_state_age_minutes is not None else "",
            retain=True,
        )
        self._publish(
            f"{self.prefix}/health/ha_state_status",
            _status(ha_state_age_minutes),
            retain=True,
        )

    def publish_control_state(
        self,
        auto_control_enabled: bool,
        extra_buffer_kwh: float,
        commanded_mode: str | None,
        evening_reserve_soc: float | None,
        last_command_info: str | None,
    ) -> None:
        """Publish inverter control state entities."""
        self._publish(
            f"{self.prefix}/control/auto_control/state",
            "ON" if auto_control_enabled else "OFF",
            retain=True,
        )
        self._publish(
            f"{self.prefix}/control/extra_buffer/state",
            f"{extra_buffer_kwh:.1f}",
            retain=True,
        )
        self._publish(
            f"{self.prefix}/control/commanded_mode",
            commanded_mode or "unknown",
            retain=True,
        )
        self._publish(
            f"{self.prefix}/control/evening_reserve_soc",
            f"{evening_reserve_soc:.0%}" if evening_reserve_soc is not None else "N/A",
            retain=True,
        )
        self._publish(
            f"{self.prefix}/control/last_command",
            last_command_info or "No commands sent",
            retain=True,
        )

    def publish_overnight_target(
        self,
        target: object | None,
    ) -> None:
        """Publish solar-aware overnight charge target sensors."""
        if target is not None:
            target_pct = target.target_soc_pct * 100
            seasonal_pct = target.seasonal_max_pct * 100
            self._publish(
                f"{self.prefix}/control/overnight_charge_target",
                f"{target_pct:.0f}",
                retain=True,
            )
            self._publish(
                f"{self.prefix}/control/overnight_solar_slots",
                str(target.solar_opportunity_slots),
                retain=True,
            )
            self._publish(
                f"{self.prefix}/control/overnight_savings",
                f"{target.estimated_savings_pence:.1f}",
                retain=True,
            )
            # Build human-readable detail
            if target.headroom_kwh > 0:
                detail = (
                    f"Charging to {target_pct:.0f}% (reduced from {seasonal_pct:.0f}%) — "
                    f"{target.solar_opportunity_slots} low-rate solar slots tomorrow, "
                    f"leaving {target.headroom_kwh:.1f} kWh headroom for free solar charging. "
                    f"Est. saving {target.estimated_savings_pence:.1f}p on overnight import."
                )
            else:
                detail = (
                    f"Full charge to {target_pct:.0f}% — "
                    f"tomorrow's export rates are strong, limited solar charging opportunity."
                )
            self._publish(
                f"{self.prefix}/control/overnight_detail",
                detail,
                retain=True,
            )
        else:
            self._publish(
                f"{self.prefix}/control/overnight_charge_target", "N/A", retain=True,
            )
            self._publish(
                f"{self.prefix}/control/overnight_solar_slots", "0", retain=True,
            )
            self._publish(
                f"{self.prefix}/control/overnight_savings", "0.0", retain=True,
            )
            self._publish(
                f"{self.prefix}/control/overnight_detail",
                "Solar overnight charging disabled or no rate data available.",
                retain=True,
            )

    def subscribe_kill_switch(self, on_toggle: Callable[[bool], None]) -> None:
        """Subscribe to auto control kill switch command topic."""
        topic = f"{self.prefix}/control/auto_control/set"

        def _on_message(
            client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage
        ) -> None:
            payload = msg.payload.decode().upper()
            enabled = payload in ("ON", "TRUE", "1")
            logger.info("Kill switch toggled: %s", payload)
            on_toggle(enabled)
            # Echo back state
            self._publish(
                f"{self.prefix}/control/auto_control/state",
                "ON" if enabled else "OFF",
                retain=True,
            )

        self._client.subscribe(topic)
        self._client.message_callback_add(topic, _on_message)
        logger.info("Subscribed to kill switch: %s", topic)

    def subscribe_buffer(self, on_change: Callable[[float], None]) -> None:
        """Subscribe to extra buffer slider command topic."""
        topic = f"{self.prefix}/control/extra_buffer/set"

        def _on_message(
            client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage
        ) -> None:
            try:
                value = float(msg.payload.decode())
                logger.info("Buffer slider changed: %.1f kWh", value)
                on_change(value)
                self._publish(
                    f"{self.prefix}/control/extra_buffer/state",
                    f"{value:.1f}",
                    retain=True,
                )
            except ValueError:
                logger.warning("Invalid buffer value: %s", msg.payload)

        self._client.subscribe(topic)
        self._client.message_callback_add(topic, _on_message)
        logger.info("Subscribed to buffer slider: %s", topic)

    def _publish(self, topic: str, payload: str, retain: bool = False) -> None:
        """Publish a message to MQTT."""
        self._client.publish(topic, payload, retain=retain)

    def _publish_discovery(self) -> None:
        """Publish HA MQTT discovery config for all entities."""
        sensors = [
            ("export_rate", "rates/export/current", "Export Rate", "p/kWh", "mdi:currency-gbp"),
            ("export_rate_slot", "rates/export/current_slot", "Export Rate Slot", None, "mdi:clock-outline"),
            ("best_upcoming_rate", "rates/export/best_upcoming", "Best Upcoming Export Rate", "p/kWh", "mdi:chart-line"),
            ("best_upcoming_time", "rates/export/best_upcoming_time", "Best Upcoming Export Time", None, "mdi:clock-outline"),
            ("recommendation_state", "recommendation/state", "Recommendation", None, "mdi:lightbulb-on"),
            ("recommendation_explanation", "recommendation/explanation", "Recommendation Detail", None, "mdi:text"),
            ("recommendation_reason", "recommendation/reason_code", "Recommendation Reason", None, "mdi:tag"),
            ("recommendation_mode", "recommendation/mode", "Recommendation Mode", None, "mdi:tune"),
            ("today_actual_revenue", "revenue/today/actual_pence", "Today Export Revenue", "p", "mdi:cash"),
            ("today_flat_revenue", "revenue/today/flat_pence", "Today Flat Baseline Revenue", "p", "mdi:cash-minus"),
            ("today_uplift", "revenue/today/uplift_pence", "Today Uplift vs Flat", "p", "mdi:cash-plus"),
            ("today_export_kwh", "revenue/today/export_kwh", "Today Exported", "kWh", "mdi:lightning-bolt"),
            ("today_import_cost", "revenue/today/import_cost_pence", "Today Import Cost", "p", "mdi:cash-remove"),
            ("today_import_kwh", "revenue/today/import_kwh", "Today Imported", "kWh", "mdi:lightning-bolt-outline"),
            ("today_net_revenue", "revenue/today/net_revenue_pence", "Today Net Revenue", "p", "mdi:cash-check"),
            ("today_charging_cost", "revenue/today/charging_cost_pence", "Today Charging Opportunity Cost", "p", "mdi:battery-charging"),
            ("today_true_profit", "revenue/today/true_profit_pence", "Today True Profit", "p", "mdi:cash-lock"),
            ("today_revenue_source", "revenue/today/source", "Today Revenue Source", None, "mdi:information-outline"),
            ("month_actual_revenue", "revenue/month/actual_pence", "Month Export Revenue", "p", "mdi:cash"),
            ("month_flat_revenue", "revenue/month/flat_pence", "Month Flat Baseline Revenue", "p", "mdi:cash-minus"),
            ("month_uplift", "revenue/month/uplift_pence", "Month Uplift vs Flat", "p", "mdi:cash-plus"),
            ("month_export_kwh", "revenue/month/export_kwh", "Month Exported", "kWh", "mdi:lightning-bolt"),
            ("month_import_cost", "revenue/month/import_cost_pence", "Month Import Cost", "p", "mdi:cash-remove"),
            ("month_import_kwh", "revenue/month/import_kwh", "Month Imported", "kWh", "mdi:lightning-bolt-outline"),
            ("month_net_revenue", "revenue/month/net_revenue_pence", "Month Net Revenue", "p", "mdi:cash-check"),
            ("month_charging_cost", "revenue/month/charging_cost_pence", "Month Charging Opportunity Cost", "p", "mdi:battery-charging"),
            ("month_true_profit", "revenue/month/true_profit_pence", "Month True Profit", "p", "mdi:cash-lock"),
            ("7d_actual_revenue", "revenue/7d/actual_pence", "7-Day Export Revenue", "p", "mdi:cash"),
            ("7d_uplift", "revenue/7d/uplift_pence", "7-Day Uplift vs Flat", "p", "mdi:cash-plus"),
            ("7d_export_kwh", "revenue/7d/export_kwh", "7-Day Exported", "kWh", "mdi:lightning-bolt"),
            ("7d_avg_rate", "revenue/7d/avg_rate", "7-Day Avg Rate", "p/kWh", "mdi:chart-line"),
            ("30d_actual_revenue", "revenue/30d/actual_pence", "30-Day Export Revenue", "p", "mdi:cash"),
            ("30d_uplift", "revenue/30d/uplift_pence", "30-Day Uplift vs Flat", "p", "mdi:cash-plus"),
            ("30d_export_kwh", "revenue/30d/export_kwh", "30-Day Exported", "kWh", "mdi:lightning-bolt"),
            ("30d_avg_rate", "revenue/30d/avg_rate", "30-Day Avg Rate", "p/kWh", "mdi:chart-line"),
            ("battery_soc", "battery/soc", "Optimizer Battery SoC", "%", "mdi:battery"),
            ("pv_power", "solar/pv_power", "Optimizer PV Power", "kW", "mdi:solar-power"),
            ("feed_in", "solar/feed_in", "Optimizer Feed-in", "kW", "mdi:transmission-tower-export"),
            ("last_run", "service/last_run", "Optimizer Last Run", None, "mdi:clock-outline"),
            ("import_rate", "rates/import/current", "Import Rate", "p/kWh", "mdi:currency-gbp"),
            ("tariff_age", "health/tariff_age", "Tariff Data Age", "min", "mdi:clock-alert-outline"),
            ("tariff_status", "health/tariff_status", "Tariff Data Status", None, "mdi:cloud-check"),
            ("ha_state_age", "health/ha_state_age", "HA State Age", "min", "mdi:clock-alert-outline"),
            ("ha_state_status", "health/ha_state_status", "HA State Status", None, "mdi:home-heart"),
            ("plan_discharge_kw", "plan/discharge_kw", "Plan Discharge Power", "kW", "mdi:lightning-bolt-circle"),
            ("plan_status", "plan/status", "Export Plan Status", None, "mdi:calendar-clock"),
            ("plan_slots_planned", "plan/slots_planned", "Plan Slots Scheduled", None, "mdi:calendar-multiple"),
            ("target_max_soc", "control/target_max_soc", "Target Max SoC", "%", "mdi:battery-charging-high"),
            ("plan_next_slot_time", "plan/next_slot_time", "Next Planned Slot", None, "mdi:clock-start"),
            ("plan_next_slot_rate", "plan/next_slot_rate", "Next Planned Slot Rate", "p/kWh", "mdi:cash-clock"),
            ("overnight_charge_target", "control/overnight_charge_target", "Overnight Charge Target", "%", "mdi:battery-clock"),
            ("overnight_solar_slots", "control/overnight_solar_slots", "Overnight Solar Slots", None, "mdi:weather-sunny"),
            ("overnight_savings", "control/overnight_savings", "Overnight Est. Savings", "p", "mdi:piggy-bank"),
            ("overnight_detail", "control/overnight_detail", "Overnight Charge Detail", None, "mdi:text"),
        ]

        # Schedule sensors need json_attributes_topic (payload exceeds 255-char state limit)
        schedule_sensors = [
            ("export_rate_schedule", "rates/export/schedule", "Export Rate Schedule", "mdi:chart-bar"),
            ("import_rate_schedule", "rates/import/schedule", "Import Rate Schedule", "mdi:chart-bar"),
            ("upcoming_export_schedule", "rates/export/upcoming_schedule", "Upcoming Export Rates", "mdi:chart-timeline-variant"),
            ("upcoming_import_schedule", "rates/import/upcoming_schedule", "Upcoming Import Rates", "mdi:chart-timeline-variant"),
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
            if object_id == "plan_next_slot_time":
                config["device_class"] = "timestamp"
            topic = (
                f"{self.discovery_prefix}/sensor"
                f"/octopus_export_optimizer/{object_id}/config"
            )
            self._publish(topic, json.dumps(config), retain=True)

        for object_id, state_suffix, name, icon in schedule_sensors:
            config = {
                "name": name,
                "state_topic": f"{self.prefix}/{state_suffix}/state",
                "json_attributes_topic": f"{self.prefix}/{state_suffix}",
                "unique_id": f"octopus_export_optimizer_{object_id}",
                "device": DEVICE_INFO,
                "icon": icon,
            }
            topic = (
                f"{self.discovery_prefix}/sensor"
                f"/octopus_export_optimizer/{object_id}/config"
            )
            self._publish(topic, json.dumps(config), retain=True)

        # Control sensors
        control_sensors = [
            ("commanded_mode", "control/commanded_mode", "Commanded Work Mode", None, "mdi:tune"),
            ("evening_reserve_soc", "control/evening_reserve_soc", "Evening Reserve SoC", None, "mdi:battery-alert"),
            ("last_command", "control/last_command", "Last Inverter Command", None, "mdi:history"),
        ]
        for object_id, state_suffix, name, unit, icon in control_sensors:
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

        # Kill switch (MQTT switch entity, default OFF)
        switch_config = {
            "name": "Auto Inverter Control",
            "state_topic": f"{self.prefix}/control/auto_control/state",
            "command_topic": f"{self.prefix}/control/auto_control/set",
            "unique_id": "octopus_export_optimizer_auto_control",
            "device": DEVICE_INFO,
            "icon": "mdi:robot",
            "payload_on": "ON",
            "payload_off": "OFF",
        }
        topic = (
            f"{self.discovery_prefix}/switch"
            f"/octopus_export_optimizer/auto_control/config"
        )
        self._publish(topic, json.dumps(switch_config), retain=True)

        # Buffer slider (MQTT number entity, 0-10 kWh)
        number_config = {
            "name": "Extra Evening Buffer",
            "state_topic": f"{self.prefix}/control/extra_buffer/state",
            "command_topic": f"{self.prefix}/control/extra_buffer/set",
            "unique_id": "octopus_export_optimizer_extra_buffer",
            "device": DEVICE_INFO,
            "icon": "mdi:pot-steam",
            "min": 0,
            "max": 10,
            "step": 0.5,
            "unit_of_measurement": "kWh",
        }
        topic = (
            f"{self.discovery_prefix}/number"
            f"/octopus_export_optimizer/extra_buffer/config"
        )
        self._publish(topic, json.dumps(number_config), retain=True)

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
