"""Charge planner: identifies optimal solar charging windows.

Analyses upcoming export rates to find low-rate periods during solar
hours where the battery should charge from solar (max_soc=100%)
rather than exporting at poor rates, storing energy for later
high-rate discharge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from octopus_export_optimizer.models.export_plan import ExportPlan
from octopus_export_optimizer.models.tariff import TariffSlot

# Solar generation hours (UTC) — roughly UK sunrise to late afternoon
_SOLAR_HOURS_START = 6
_SOLAR_HOURS_END = 16


@dataclass(frozen=True)
class ChargingSlot:
    """A single slot identified as optimal for solar charging."""

    interval_start: datetime
    interval_end: datetime
    export_rate_pence: float
    value_of_storage_pence: float  # breakeven - export_rate


@dataclass(frozen=True)
class ChargePlan:
    """Charging windows where max_soc should be raised to 100%.

    Built by the charge planner, consumed by engine.evaluate()
    to override max_soc during low-rate solar periods.
    Recalculated every 60s — not persisted.
    """

    charging_slots: list[ChargingSlot]
    target_discharge_rate_pence: float
    breakeven_rate_pence: float
    headroom_kwh: float

    def is_charging_now(self, now: datetime) -> bool:
        """Return True if now falls within a charging window."""
        return any(
            s.interval_start <= now < s.interval_end
            for s in self.charging_slots
        )

    def get_current_slot(self, now: datetime) -> ChargingSlot | None:
        """Return the charging slot covering the current time, or None."""
        for s in self.charging_slots:
            if s.interval_start <= now < s.interval_end:
                return s
        return None


def build_charge_plan(
    now: datetime,
    upcoming_slots: list[TariffSlot],
    export_plan: ExportPlan | None,
    battery_headroom_kwh: float,
    round_trip_efficiency: float,
    export_threshold_pence: float,
    solar_charge_kwh_per_slot: float = 0.75,
) -> ChargePlan | None:
    """Identify low-rate solar windows where battery should charge to 100%.

    Selects only the cheapest slots needed to fill the battery headroom,
    so solar is exported at mediocre rates and stored only during the
    poorest-rate periods.

    Args:
        now: Current UTC time.
        upcoming_slots: All upcoming export tariff slots.
        export_plan: The current discharge plan (if any).
        battery_headroom_kwh: Room to charge from current SoC to 100%.
        round_trip_efficiency: Battery round-trip efficiency (0-1).
        export_threshold_pence: Minimum rate for profitable export.
        solar_charge_kwh_per_slot: Conservative kWh absorbed per 30-min slot.

    Returns:
        A ChargePlan if charging windows are identified, None otherwise.
    """
    if battery_headroom_kwh < 0.1 or not upcoming_slots:
        return None

    discharge_eff = round_trip_efficiency ** 0.5

    # Determine the rate we expect to sell stored energy at
    if export_plan and export_plan.planned_slots:
        target_rate = sum(
            s.rate_pence for s in export_plan.planned_slots
        ) / len(export_plan.planned_slots)
        first_discharge = min(
            s.interval_start for s in export_plan.planned_slots
        )
    else:
        # No discharge plan — use best upcoming rate above threshold
        eligible = [
            s for s in upcoming_slots
            if s.rate_inc_vat_pence >= export_threshold_pence
            and s.interval_end > now
        ]
        if not eligible:
            return None
        best = max(eligible, key=lambda s: s.rate_inc_vat_pence)
        target_rate = best.rate_inc_vat_pence
        first_discharge = best.interval_start

    breakeven = target_rate * discharge_eff

    # Find all eligible slots: below breakeven, solar hours, before discharge
    candidates = []
    for slot in upcoming_slots:
        if slot.interval_end <= now:
            continue
        if slot.interval_start >= first_discharge:
            continue
        slot_hour = slot.interval_start.hour
        if not (_SOLAR_HOURS_START <= slot_hour < _SOLAR_HOURS_END):
            continue
        if slot.rate_inc_vat_pence < breakeven:
            candidates.append(slot)

    if not candidates:
        return None

    # Pick only the N cheapest slots needed to fill headroom
    slots_needed = max(1, int(
        (battery_headroom_kwh + solar_charge_kwh_per_slot - 0.01)
        / solar_charge_kwh_per_slot
    ))
    candidates.sort(key=lambda s: s.rate_inc_vat_pence)
    selected = candidates[:slots_needed]

    charging_slots = [
        ChargingSlot(
            interval_start=s.interval_start,
            interval_end=s.interval_end,
            export_rate_pence=s.rate_inc_vat_pence,
            value_of_storage_pence=round(breakeven - s.rate_inc_vat_pence, 2),
        )
        for s in selected
    ]
    charging_slots.sort(key=lambda s: s.interval_start)

    return ChargePlan(
        charging_slots=charging_slots,
        target_discharge_rate_pence=round(target_rate, 2),
        breakeven_rate_pence=round(breakeven, 2),
        headroom_kwh=round(battery_headroom_kwh, 2),
    )
