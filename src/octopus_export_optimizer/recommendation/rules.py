"""Individual recommendation rules.

Each rule is a standalone function that evaluates a specific
business condition and returns a Recommendation if it applies,
or None if it doesn't.

Rules are evaluated in priority order by the engine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from octopus_export_optimizer.config.settings import BatterySettings, ThresholdSettings
from octopus_export_optimizer.models.recommendation import (
    Recommendation,
    RecommendationInputSnapshot,
)
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState


class Rule:
    """Base class for recommendation rules."""

    def __init__(
        self,
        thresholds: ThresholdSettings,
        battery: BatterySettings,
    ) -> None:
        self.thresholds = thresholds
        self.battery = battery

    def evaluate(self, snapshot: RecommendationInputSnapshot) -> Recommendation | None:
        raise NotImplementedError

    @staticmethod
    def _normalize_soc(soc_pct: float) -> float:
        """Normalize SoC percentage (0-100) to fraction (0.0-1.0)."""
        return soc_pct / 100.0

    def _make_recommendation(
        self,
        snapshot: RecommendationInputSnapshot,
        state: RecommendationState,
        reason_code: ReasonCode,
        explanation: str,
        battery_aware: bool = False,
        valid_minutes: int = 30,
    ) -> Recommendation:
        return Recommendation(
            timestamp=snapshot.timestamp,
            state=state,
            reason_code=reason_code,
            explanation=explanation,
            battery_aware=battery_aware,
            valid_until=snapshot.timestamp + timedelta(minutes=valid_minutes),
            input_snapshot_id=snapshot.id,
        )


class InsufficientDataRule(Rule):
    """Return INSUFFICIENT_DATA if tariff data is missing or stale."""

    def evaluate(self, snapshot: RecommendationInputSnapshot) -> Recommendation | None:
        if snapshot.current_export_rate_pence is None:
            return self._make_recommendation(
                snapshot,
                RecommendationState.INSUFFICIENT_DATA,
                ReasonCode.NO_TARIFF_DATA,
                "No current export tariff rate available. "
                "Cannot make a recommendation without tariff data.",
            )

        if snapshot.upcoming_rates_count == 0:
            return self._make_recommendation(
                snapshot,
                RecommendationState.INSUFFICIENT_DATA,
                ReasonCode.NO_TARIFF_DATA,
                "No upcoming export rates available. "
                "Look-ahead window is empty.",
            )

        if (
            snapshot.tariff_data_age_minutes is not None
            and snapshot.tariff_data_age_minutes
            > self.thresholds.data_freshness_limit_minutes
        ):
            return self._make_recommendation(
                snapshot,
                RecommendationState.INSUFFICIENT_DATA,
                ReasonCode.STALE_TARIFF_DATA,
                f"Tariff data is {snapshot.tariff_data_age_minutes:.0f} minutes old "
                f"(limit: {self.thresholds.data_freshness_limit_minutes:.0f} min). "
                f"Internet may be down — using stale rates is unsafe.",
            )

        return None


class OvernightChargeRule(Rule):
    """Always charge during cheap import hours (e.g. Intelligent Go overnight).

    This ensures the battery is charged overnight for self-consumption
    and potential export the next day, regardless of export rates.
    """

    def __init__(
        self,
        thresholds: ThresholdSettings,
        battery: BatterySettings,
        cheap_rate_start_hour: float = 23.5,
        cheap_rate_end_hour: float = 5.5,
        target_soc_pct: float = 0.95,
    ) -> None:
        super().__init__(thresholds, battery)
        self.cheap_rate_start_hour = cheap_rate_start_hour
        self.cheap_rate_end_hour = cheap_rate_end_hour
        self.target_soc_pct = target_soc_pct

    def _is_cheap_window(self, now: datetime) -> bool:
        """Check if current time is within cheap import hours."""
        hour = now.hour + now.minute / 60.0
        if self.cheap_rate_start_hour > self.cheap_rate_end_hour:
            # Overnight window (e.g. 23:30 to 05:30)
            return hour >= self.cheap_rate_start_hour or hour < self.cheap_rate_end_hour
        return self.cheap_rate_start_hour <= hour < self.cheap_rate_end_hour

    def evaluate(self, snapshot: RecommendationInputSnapshot) -> Recommendation | None:
        if not self._is_cheap_window(snapshot.timestamp):
            return None

        if snapshot.battery_soc_pct is None:
            return None

        soc = self._normalize_soc(snapshot.battery_soc_pct)
        if soc >= self.target_soc_pct:
            return None  # Already charged enough

        return self._make_recommendation(
            snapshot,
            RecommendationState.CHARGE_FOR_LATER_EXPORT,
            ReasonCode.OVERNIGHT_CHARGE_STRATEGY,
            f"Cheap import window active. "
            f"Battery at {soc:.0%} — charging to {self.target_soc_pct:.0%} "
            f"for tomorrow's self-consumption and export.",
            battery_aware=True,
        )


class ChargeForLaterExportRule(Rule):
    """Recommend charging when cheap import + strong upcoming export."""

    def evaluate(self, snapshot: RecommendationInputSnapshot) -> Recommendation | None:
        if not self.thresholds.allow_import_arbitrage:
            return None

        if snapshot.best_upcoming_rate_pence is None:
            return None

        if snapshot.current_import_rate_pence is None:
            return None

        if snapshot.battery_soc_pct is None:
            return None

        is_cheap_import = (
            snapshot.current_import_rate_pence
            <= self.thresholds.cheap_import_threshold_pence
        )
        is_strong_upcoming = (
            snapshot.best_upcoming_rate_pence
            >= self.thresholds.export_now_threshold_pence * 1.5
        )
        soc = self._normalize_soc(snapshot.battery_soc_pct)
        has_headroom = soc < 0.95

        if is_cheap_import and is_strong_upcoming and has_headroom:
            return self._make_recommendation(
                snapshot,
                RecommendationState.CHARGE_FOR_LATER_EXPORT,
                ReasonCode.CHEAP_IMPORT_HIGH_EXPORT_LATER,
                f"Import rate is cheap at {snapshot.current_import_rate_pence:.1f}p/kWh. "
                f"Strong export rate of {snapshot.best_upcoming_rate_pence:.1f}p/kWh coming "
                f"at {snapshot.best_upcoming_slot_start}. "
                f"Battery at {soc:.0%} - charging to capture arbitrage.",
                battery_aware=True,
            )

        return None


class PlannedExportRule(Rule):
    """Use export plan to decide whether to export or hold.

    When an export plan is active, this rule fires for planned slots
    (EXPORT_NOW with target discharge power) and holds during gaps
    between planned slots when the rate is above threshold.
    """

    def __init__(
        self,
        thresholds: ThresholdSettings,
        battery: BatterySettings,
        export_plan,
    ) -> None:
        super().__init__(thresholds, battery)
        self.plan = export_plan

    def evaluate(self, snapshot: RecommendationInputSnapshot) -> Recommendation | None:
        if self.plan is None:
            return None

        current_slot = self.plan.get_current_slot(snapshot.timestamp)

        if current_slot is not None:
            # Check SOC is still above reserve
            if snapshot.battery_soc_pct is not None:
                soc = self._normalize_soc(snapshot.battery_soc_pct)
                if soc <= self.thresholds.reserve_soc_floor:
                    return None  # Battery depleted, fall through

            rec = self._make_recommendation(
                snapshot,
                RecommendationState.EXPORT_NOW,
                ReasonCode.PLANNED_EXPORT,
                f"Export plan: discharging at {current_slot.discharge_kw:.1f}kW "
                f"during {current_slot.rate_pence:.1f}p/kWh slot "
                f"({len(self.plan.planned_slots)} slots planned, "
                f"{self.plan.total_planned_kwh:.1f}kWh total).",
                battery_aware=True,
            )
            rec.target_discharge_kw = current_slot.discharge_kw
            rec.export_plan_slots = len(self.plan.planned_slots)
            return rec

        # Not in a planned slot — should we hold for one?
        next_slot = self.plan.get_next_slot(snapshot.timestamp)
        if next_slot is not None and snapshot.current_export_rate_pence is not None:
            rate = snapshot.current_export_rate_pence
            if rate >= self.thresholds.export_now_threshold_pence:
                return self._make_recommendation(
                    snapshot,
                    RecommendationState.HOLD_BATTERY,
                    ReasonCode.PLANNED_HOLD,
                    f"Rate {rate:.1f}p/kWh is above threshold but holding "
                    f"for planned slot at {next_slot.rate_pence:.1f}p/kWh "
                    f"starting {next_slot.interval_start.strftime('%H:%M')}.",
                    battery_aware=True,
                )

        return None  # No plan opinion, fall through


class ExportNowRule(Rule):
    """Recommend exporting when rate is high and no better slot ahead."""

    def evaluate(self, snapshot: RecommendationInputSnapshot) -> Recommendation | None:
        if snapshot.current_export_rate_pence is None:
            return None

        rate = snapshot.current_export_rate_pence
        threshold = self.thresholds.export_now_threshold_pence

        if rate < threshold:
            return None

        # Check if a meaningfully better slot exists later
        if snapshot.best_upcoming_rate_pence is not None:
            delta = snapshot.best_upcoming_rate_pence - rate
            if delta > self.thresholds.better_slot_delta_pence:
                # Better slot exists — but if we're already above threshold
                # and battery has enough capacity for both slots, export now.
                if (
                    snapshot.battery_soc_pct is not None
                    and snapshot.exportable_battery_kwh is not None
                    and snapshot.exportable_battery_kwh
                    > self.battery.capacity_kwh * 0.15
                ):
                    soc = self._normalize_soc(snapshot.battery_soc_pct)
                    exportable = snapshot.exportable_battery_kwh
                    return self._make_recommendation(
                        snapshot,
                        RecommendationState.EXPORT_NOW,
                        ReasonCode.HIGH_RATE_WITH_BATTERY,
                        f"Export rate is {rate:.1f}p/kWh (above {threshold:.1f}p threshold). "
                        f"Better slot at {snapshot.best_upcoming_rate_pence:.1f}p/kWh coming, "
                        f"but battery at {soc:.0%} with ~{exportable:.1f}kWh "
                        f"has enough capacity for both.",
                        battery_aware=True,
                    )
                return None  # Low battery — hold for the better slot

        # Check battery state if available
        battery_aware = snapshot.battery_soc_pct is not None
        if battery_aware:
            soc = self._normalize_soc(snapshot.battery_soc_pct)
            if soc < self.thresholds.minimum_soc_for_export:
                # Battery too low to export — check if solar is feeding in
                if snapshot.feed_in_kw is not None and snapshot.feed_in_kw > self.thresholds.minimum_meaningful_export_kw:
                    return self._make_recommendation(
                        snapshot,
                        RecommendationState.EXPORT_NOW,
                        ReasonCode.HIGH_RATE_SOLAR_EXPORT,
                        f"Export rate is {rate:.1f}p/kWh (above {threshold:.1f}p threshold). "
                        f"Battery SoC too low for discharge ({soc:.0%}), "
                        f"but solar feed-in of {snapshot.feed_in_kw:.1f}kW is being exported.",
                        battery_aware=True,
                    )
                return None  # Not enough to export

            # Battery has enough charge
            exportable = snapshot.exportable_battery_kwh or 0
            return self._make_recommendation(
                snapshot,
                RecommendationState.EXPORT_NOW,
                ReasonCode.HIGH_RATE_WITH_BATTERY,
                f"Export rate is {rate:.1f}p/kWh (above {threshold:.1f}p threshold). "
                f"No meaningfully better slot ahead. "
                f"Battery at {soc:.0%} with "
                f"~{exportable:.1f}kWh exportable above reserve.",
                battery_aware=True,
            )

        # Tariff-only recommendation (no battery data)
        return self._make_recommendation(
            snapshot,
            RecommendationState.EXPORT_NOW,
            ReasonCode.HIGH_EXPORT_RATE,
            f"Export rate is {rate:.1f}p/kWh (above {threshold:.1f}p threshold). "
            f"No meaningfully better slot ahead.",
        )


class HoldBatteryRule(Rule):
    """Recommend holding when a better slot is coming."""

    def evaluate(self, snapshot: RecommendationInputSnapshot) -> Recommendation | None:
        if snapshot.current_export_rate_pence is None:
            return None

        if snapshot.best_upcoming_rate_pence is None:
            return None

        rate = snapshot.current_export_rate_pence
        best_upcoming = snapshot.best_upcoming_rate_pence
        delta = best_upcoming - rate

        if delta <= self.thresholds.better_slot_delta_pence:
            return None

        # Current rate should be at least somewhat attractive
        if rate < self.thresholds.export_now_threshold_pence * 0.5:
            return None

        # Check battery state
        battery_aware = snapshot.battery_soc_pct is not None
        if battery_aware:
            soc = self._normalize_soc(snapshot.battery_soc_pct)
            if soc < self.thresholds.reserve_soc_floor:
                return None  # Nothing to hold

            # Consider remaining generation opportunity
            gen_factor = snapshot.remaining_generation_heuristic
            gen_note = ""
            if gen_factor is not None and gen_factor > 0.3:
                gen_note = (
                    f" Remaining generation opportunity is moderate ({gen_factor:.0%}), "
                    f"so holding battery headroom may also capture incoming solar."
                )

            return self._make_recommendation(
                snapshot,
                RecommendationState.HOLD_BATTERY,
                ReasonCode.BETTER_SLOT_HIGH_SOC,
                f"Current rate is {rate:.1f}p/kWh but a better slot of "
                f"{best_upcoming:.1f}p/kWh is coming at {snapshot.best_upcoming_slot_start}. "
                f"Battery at {soc:.0%} - holding for better rate.{gen_note}",
                battery_aware=True,
            )

        # Tariff-only hold
        return self._make_recommendation(
            snapshot,
            RecommendationState.HOLD_BATTERY,
            ReasonCode.BETTER_SLOT_COMING,
            f"Current rate is {rate:.1f}p/kWh but a better slot of "
            f"{best_upcoming:.1f}p/kWh is coming at {snapshot.best_upcoming_slot_start}. "
            f"Consider holding battery for the better rate.",
        )


class NormalSelfConsumptionRule(Rule):
    """Default fallback: normal self-consumption."""

    def evaluate(self, snapshot: RecommendationInputSnapshot) -> Recommendation | None:
        rate = snapshot.current_export_rate_pence
        if rate is not None and rate < self.thresholds.export_now_threshold_pence:
            return self._make_recommendation(
                snapshot,
                RecommendationState.NORMAL_SELF_CONSUMPTION,
                ReasonCode.LOW_EXPORT_RATE,
                f"Current export rate is {rate:.1f}p/kWh, below the "
                f"{self.thresholds.export_now_threshold_pence:.1f}p threshold. "
                f"No special action recommended — operate normally.",
            )

        return self._make_recommendation(
            snapshot,
            RecommendationState.NORMAL_SELF_CONSUMPTION,
            ReasonCode.DEFAULT_OPERATION,
            "No specific export opportunity identified. Operating normally.",
        )
