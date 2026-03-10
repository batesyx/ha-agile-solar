"""Recommendation engine — pure-function rule evaluator.

The engine takes a RecommendationInputSnapshot and evaluates
rules in priority order. The first rule that returns a
Recommendation wins. A fallback rule always fires.

The engine has no side effects — no database, no I/O.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from octopus_export_optimizer.config.settings import (
    BatterySettings,
    InverterControlSettings,
    ThresholdSettings,
)
from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.models.recommendation import (
    Recommendation,
    RecommendationInputSnapshot,
)
from octopus_export_optimizer.models.tariff import TariffSlot
from octopus_export_optimizer.models.export_plan import ExportPlan
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from octopus_export_optimizer.recommendation.rules import (
    ChargeForLaterExportRule,
    ExportNowRule,
    HoldBatteryRule,
    InsufficientDataRule,
    NormalSelfConsumptionRule,
    OvernightChargeRule,
    PlannedExportRule,
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
        inverter_control: InverterControlSettings | None = None,
    ) -> None:
        self.thresholds = thresholds
        self.battery = battery
        self.inverter_control = inverter_control or InverterControlSettings()
        self.rules: list[Rule] = [
            InsufficientDataRule(thresholds, battery),
            OvernightChargeRule(
                thresholds,
                battery,
                cheap_rate_start_hour=self.inverter_control.cheap_rate_start_hour,
                cheap_rate_end_hour=self.inverter_control.cheap_rate_end_hour,
            ),
            ChargeForLaterExportRule(thresholds, battery),
            ExportNowRule(thresholds, battery),
            HoldBatteryRule(thresholds, battery),
            NormalSelfConsumptionRule(thresholds, battery),
        ]

    def evaluate(
        self,
        snapshot: RecommendationInputSnapshot,
        upcoming_12h_rates: list[TariffSlot] | None = None,
        export_plan: ExportPlan | None = None,
    ) -> Recommendation:
        """Evaluate rules and return a recommendation.

        Always returns a recommendation — the fallback rule
        (NormalSelfConsumption) guarantees this.

        If export_plan is provided, PlannedExportRule is inserted
        before ExportNowRule to enable multi-slot discharge planning.

        If upcoming_12h_rates is provided, calculates target_max_soc:
        100% if the earliest high-rate slot is within
        full_charge_lead_time_hours, else 90%.
        """
        # Build rules list, inserting PlannedExportRule when a plan exists
        rules = list(self.rules)
        if export_plan is not None:
            planned_rule = PlannedExportRule(
                self.thresholds, self.battery, export_plan
            )
            # Insert before ExportNowRule (after ChargeForLaterExportRule)
            insert_idx = next(
                (i for i, r in enumerate(rules) if isinstance(r, ExportNowRule)),
                len(rules),
            )
            rules.insert(insert_idx, planned_rule)

        for rule in rules:
            result = rule.evaluate(snapshot)
            if result is not None:
                logger.info(
                    "Recommendation: %s (%s) — %s",
                    result.state, result.reason_code, rule.__class__.__name__,
                )
                break
        else:
            # Should never reach here due to fallback rule, but just in case
            result = Recommendation(
                timestamp=snapshot.timestamp,
                state=RecommendationState.NORMAL_SELF_CONSUMPTION,
                reason_code=ReasonCode.DEFAULT_OPERATION,
                explanation="Fallback: no rule matched.",
                battery_aware=False,
                input_snapshot_id=snapshot.id,
            )

        # Ensure controlled discharge when an export plan is active.
        # If ExportNowRule (or another rule) fired instead of PlannedExportRule,
        # target_discharge_kw won't be set — apply the plan's power target.
        if (
            export_plan is not None
            and result.target_discharge_kw is None
            and result.state == RecommendationState.EXPORT_NOW
        ):
            result.target_discharge_kw = export_plan.discharge_kw
            result.export_plan_slots = len(export_plan.planned_slots)

        # Calculate target Max SoC based on upcoming export rates.
        # Only raise to 100% when the earliest high-rate slot is
        # within full_charge_lead_time_hours, to avoid sitting at
        # 100% SOC for hours (reduces battery degradation).
        # Also checks solar opportunity cost: if solar is generating,
        # only raise to 100% if the upcoming rate (after discharge
        # losses) actually beats the current direct export rate.
        if upcoming_12h_rates:
            threshold = self.inverter_control.high_export_threshold_for_full_charge
            lead_time = timedelta(
                hours=self.inverter_control.full_charge_lead_time_hours
            )
            high_rate_slots = [
                s for s in upcoming_12h_rates
                if s.rate_inc_vat_pence >= threshold
            ]
            if high_rate_slots:
                earliest = min(high_rate_slots, key=lambda s: s.interval_start)
                time_until = earliest.interval_start - snapshot.timestamp
                if time_until <= lead_time:
                    result.target_max_soc = self._max_soc_with_solar_check(
                        snapshot, earliest.rate_inc_vat_pence
                    )
                else:
                    result.target_max_soc = 90
            else:
                result.target_max_soc = 90
        else:
            result.target_max_soc = 90

        return result

    def _max_soc_with_solar_check(
        self,
        snapshot: RecommendationInputSnapshot,
        upcoming_rate: float,
    ) -> int:
        """Decide max SoC considering solar opportunity cost.

        If solar is actively generating, only raise to 100% if the
        upcoming export rate (after discharge losses) beats the current
        direct export rate. Otherwise the solar is better sold directly.
        """
        solar_generating = (
            snapshot.pv_power_kw is not None
            and snapshot.pv_power_kw > 0.5
        )
        current_rate = snapshot.current_export_rate_pence or 0.0

        if solar_generating and current_rate > 0:
            discharge_eff = self.battery.round_trip_efficiency ** 0.5
            effective_return = upcoming_rate * discharge_eff
            return 100 if effective_return > current_rate else 90

        # No solar or no current rate → no opportunity cost
        return 100

    def build_snapshot(
        self,
        now: datetime,
        current_export: TariffSlot | None,
        upcoming_exports: list[TariffSlot],
        current_import: TariffSlot | None,
        ha_state: HaStateSnapshot | None,
        remaining_generation: float | None = None,
        minimum_soc_override: float | None = None,
        tariff_data_age_minutes: float | None = None,
    ) -> RecommendationInputSnapshot:
        """Build a RecommendationInputSnapshot from available data.

        Calculates derived battery metrics (exportable energy,
        headroom) from the raw state and configuration.

        If minimum_soc_override is provided, it replaces the static
        minimum_soc_for_export threshold for reserve calculations.
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
            if battery_soc is not None and not (0 <= battery_soc <= 100):
                logger.warning(
                    "Battery SoC out of range: %.1f%%, treating as unavailable",
                    battery_soc,
                )
                battery_soc = None
            feed_in = ha_state.feed_in_kw
            pv_power = ha_state.pv_power_kw
            load_power = ha_state.load_power_kw
            bat_charge = ha_state.battery_charge_kw
            bat_discharge = ha_state.battery_discharge_kw

            if battery_soc is not None:
                soc_fraction = battery_soc / 100.0
                current_kwh = soc_fraction * self.battery.capacity_kwh
                effective_floor = (
                    max(self.thresholds.reserve_soc_floor, minimum_soc_override)
                    if minimum_soc_override is not None
                    else self.thresholds.reserve_soc_floor
                )
                reserve_kwh = effective_floor * self.battery.capacity_kwh
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
            tariff_data_age_minutes=tariff_data_age_minutes,
        )
