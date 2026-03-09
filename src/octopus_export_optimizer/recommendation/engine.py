"""Recommendation engine — pure-function rule evaluator.

The engine takes a RecommendationInputSnapshot and evaluates
rules in priority order. The first rule that returns a
Recommendation wins. A fallback rule always fires.

The engine has no side effects — no database, no I/O.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from octopus_export_optimizer.config.settings import BatterySettings, ThresholdSettings
from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.models.recommendation import (
    Recommendation,
    RecommendationInputSnapshot,
)
from octopus_export_optimizer.models.tariff import TariffSlot
from octopus_export_optimizer.recommendation.rules import (
    ChargeForLaterExportRule,
    ExportNowRule,
    HoldBatteryRule,
    InsufficientDataRule,
    NormalSelfConsumptionRule,
    Rule,
)


class RecommendationEngine:
    """Deterministic rule-based recommendation engine.

    Evaluates rules in priority order against a snapshot of
    current state and tariff data. The first matching rule wins.
    """

    def __init__(
        self,
        thresholds: ThresholdSettings,
        battery: BatterySettings,
    ) -> None:
        self.thresholds = thresholds
        self.battery = battery
        self.rules: list[Rule] = [
            InsufficientDataRule(thresholds, battery),
            ChargeForLaterExportRule(thresholds, battery),
            ExportNowRule(thresholds, battery),
            HoldBatteryRule(thresholds, battery),
            NormalSelfConsumptionRule(thresholds, battery),
        ]

    def evaluate(
        self, snapshot: RecommendationInputSnapshot
    ) -> Recommendation:
        """Evaluate rules and return a recommendation.

        Always returns a recommendation — the fallback rule
        (NormalSelfConsumption) guarantees this.
        """
        for rule in self.rules:
            result = rule.evaluate(snapshot)
            if result is not None:
                return result

        # Should never reach here due to fallback rule, but just in case
        from octopus_export_optimizer.recommendation.types import (
            ReasonCode,
            RecommendationState,
        )

        return Recommendation(
            timestamp=snapshot.timestamp,
            state=RecommendationState.NORMAL_SELF_CONSUMPTION,
            reason_code=ReasonCode.DEFAULT_OPERATION,
            explanation="Fallback: no rule matched.",
            battery_aware=False,
            input_snapshot_id=snapshot.id,
        )

    def build_snapshot(
        self,
        now: datetime,
        current_export: TariffSlot | None,
        upcoming_exports: list[TariffSlot],
        current_import: TariffSlot | None,
        ha_state: HaStateSnapshot | None,
        remaining_generation: float | None = None,
    ) -> RecommendationInputSnapshot:
        """Build a RecommendationInputSnapshot from available data.

        Calculates derived battery metrics (exportable energy,
        headroom) from the raw state and configuration.
        """
        # Find best upcoming rate
        best_upcoming_rate: float | None = None
        best_upcoming_start: datetime | None = None
        if upcoming_exports:
            best_slot = max(upcoming_exports, key=lambda s: s.rate_inc_vat_pence)
            best_upcoming_rate = best_slot.rate_inc_vat_pence
            best_upcoming_start = best_slot.interval_start

        # Battery-derived metrics
        exportable_kwh: float | None = None
        headroom_kwh: float | None = None
        battery_soc: float | None = None
        feed_in: float | None = None
        pv_power: float | None = None
        load_power: float | None = None
        bat_charge: float | None = None
        bat_discharge: float | None = None

        if ha_state:
            battery_soc = ha_state.battery_soc_pct
            feed_in = ha_state.feed_in_kw
            pv_power = ha_state.pv_power_kw
            load_power = ha_state.load_power_kw
            bat_charge = ha_state.battery_charge_kw
            bat_discharge = ha_state.battery_discharge_kw

            if battery_soc is not None:
                soc_fraction = battery_soc / 100.0 if battery_soc > 1.0 else battery_soc
                current_kwh = soc_fraction * self.battery.capacity_kwh
                reserve_kwh = self.thresholds.reserve_soc_floor * self.battery.capacity_kwh
                exportable_kwh = max(0.0, current_kwh - reserve_kwh)
                headroom_kwh = max(
                    0.0, self.battery.capacity_kwh - current_kwh
                )

        return RecommendationInputSnapshot(
            timestamp=now,
            battery_soc_pct=battery_soc,
            current_export_rate_pence=(
                current_export.rate_inc_vat_pence if current_export else None
            ),
            best_upcoming_rate_pence=best_upcoming_rate,
            best_upcoming_slot_start=best_upcoming_start,
            upcoming_rates_count=len(upcoming_exports),
            current_import_rate_pence=(
                current_import.rate_inc_vat_pence if current_import else None
            ),
            solar_estimate_kw=None,
            feed_in_kw=feed_in,
            pv_power_kw=pv_power,
            load_power_kw=load_power,
            battery_charge_kw=bat_charge,
            battery_discharge_kw=bat_discharge,
            remaining_generation_heuristic=remaining_generation,
            exportable_battery_kwh=exportable_kwh,
            battery_headroom_kwh=headroom_kwh,
        )
