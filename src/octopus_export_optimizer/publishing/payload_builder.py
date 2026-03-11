"""Build MQTT JSON payloads from domain objects."""

from __future__ import annotations

import json
from datetime import datetime

from octopus_export_optimizer.models.recommendation import Recommendation
from octopus_export_optimizer.models.revenue import RevenueSummary
from octopus_export_optimizer.models.tariff import TariffSlot


class PayloadBuilder:
    """Constructs JSON payloads for MQTT publishing to Home Assistant."""

    @staticmethod
    def rate_payload(slot: TariffSlot | None) -> str:
        """Build a rate sensor payload (simple numeric value for HA)."""
        if slot is None:
            return ""
        return f"{slot.rate_inc_vat_pence:.2f}"

    @staticmethod
    def best_upcoming_payload(
        slot: TariffSlot | None,
    ) -> str:
        """Build a best upcoming rate sensor payload (simple numeric value)."""
        if slot is None:
            return ""
        return f"{slot.rate_inc_vat_pence:.2f}"

    @staticmethod
    def recommendation_state_payload(rec: Recommendation | None) -> str:
        """Build the recommendation state string."""
        if rec is None:
            return "UNKNOWN"
        return rec.state.value

    @staticmethod
    def recommendation_explanation_payload(rec: Recommendation | None) -> str:
        """Build the recommendation explanation string."""
        if rec is None:
            return "No recommendation available"
        return rec.explanation

    @staticmethod
    def recommendation_reason_payload(rec: Recommendation | None) -> str:
        """Build the recommendation reason code string."""
        if rec is None:
            return "NONE"
        return rec.reason_code.value

    @staticmethod
    def recommendation_mode_payload(rec: Recommendation | None) -> str:
        """Build the recommendation mode (tariff-only vs battery-aware)."""
        if rec is None:
            return "unknown"
        return "battery-aware" if rec.battery_aware else "tariff-only"

    @staticmethod
    def revenue_payload(summary: RevenueSummary | None) -> dict:
        """Build revenue sensor payloads as a dict of topic_suffix -> value."""
        if summary is None:
            return {
                "actual_pence": "0.0",
                "flat_pence": "0.0",
                "uplift_pence": "0.0",
                "export_kwh": "0.0",
                "import_cost_pence": "0.0",
                "import_kwh": "0.0",
                "net_revenue_pence": "0.0",
                "charging_cost_pence": "0.0",
                "true_profit_pence": "0.0",
            }
        return {
            "actual_pence": f"{summary.agile_revenue_pence:.1f}",
            "flat_pence": f"{summary.flat_revenue_pence:.1f}",
            "uplift_pence": f"{summary.uplift_pence:.1f}",
            "export_kwh": f"{summary.total_export_kwh:.2f}",
            "import_cost_pence": f"{summary.import_cost_pence:.1f}",
            "import_kwh": f"{summary.total_import_kwh:.2f}",
            "net_revenue_pence": f"{summary.net_revenue_pence:.1f}",
            "charging_cost_pence": f"{summary.charging_opportunity_cost_pence:.1f}",
            "true_profit_pence": f"{summary.true_profit_pence:.1f}",
        }

    @staticmethod
    def rate_schedule_payload(
        slots: list[TariffSlot],
        current_time: datetime | None = None,
        planned_starts: set[str] | None = None,
        charging_starts: set[str] | None = None,
    ) -> str:
        """Build a JSON payload with today's rate schedule for charting.

        Publishes as a JSON object with 'rates' array and metadata,
        suitable for HA sensor attributes + ApexCharts data_generator.

        If planned_starts is provided, each rate entry includes a
        'planned' boolean indicating whether the slot is targeted
        by the export planner. If charging_starts is provided, a
        'charging' boolean indicates solar charging windows.
        """
        if not slots:
            return json.dumps({"rates": [], "count": 0})

        rates = []
        for slot in sorted(slots, key=lambda s: s.interval_start):
            iso = slot.interval_start.isoformat()
            entry: dict = {
                "start": slot.interval_start.strftime("%H:%M"),
                "end": slot.interval_end.strftime("%H:%M"),
                "rate": round(slot.rate_inc_vat_pence, 2),
                "start_iso": iso,
                "planned": (
                    planned_starts is not None and iso in planned_starts
                ),
                "charging": (
                    charging_starts is not None and iso in charging_starts
                ),
            }
            rates.append(entry)

        return json.dumps({
            "rates": rates,
            "count": len(rates),
            "min_rate": round(min(s.rate_inc_vat_pence for s in slots), 2),
            "max_rate": round(max(s.rate_inc_vat_pence for s in slots), 2),
            "avg_rate": round(
                sum(s.rate_inc_vat_pence for s in slots) / len(slots), 2
            ),
        })

    @staticmethod
    def service_status_payload(online: bool) -> str:
        return "online" if online else "offline"

    @staticmethod
    def timestamp_payload(dt: datetime | None) -> str:
        if dt is None:
            return ""
        return dt.isoformat()

    @staticmethod
    def float_payload(value: float | None, precision: int = 2) -> str:
        if value is None:
            return ""
        return f"{value:.{precision}f}"
