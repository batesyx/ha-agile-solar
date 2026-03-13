"""Export planner: schedules optimal battery discharge across tariff slots.

Builds an ExportPlan that spreads exportable energy across the
highest-value half-hour slots at a moderate discharge rate,
balancing revenue with battery longevity.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from octopus_export_optimizer.models.export_plan import ExportPlan, PlannedSlot
from octopus_export_optimizer.models.tariff import TariffSlot


def build_export_plan(
    now: datetime,
    upcoming_slots: list[TariffSlot],
    exportable_kwh: float,
    export_threshold_pence: float,
    max_discharge_kw: float,
    battery_capacity_kwh: float,
    round_trip_efficiency: float,
) -> ExportPlan | None:
    """Build an optimal discharge schedule from upcoming tariff slots.

    Args:
        now: Current UTC time.
        upcoming_slots: All upcoming export tariff slots (may include
            the current slot and future slots).
        exportable_kwh: Energy available above evening reserve
            (already accounts for SOC and reserve).
        export_threshold_pence: Minimum rate to consider for export.
        max_discharge_kw: Maximum comfortable discharge power.
        battery_capacity_kwh: Total battery capacity (for reference).
        round_trip_efficiency: Battery round-trip efficiency (0-1).

    Returns:
        An ExportPlan if there are eligible slots and energy to export,
        None otherwise.
    """
    if exportable_kwh <= 0 or not upcoming_slots:
        return None

    # Account for discharge losses — only one-way efficiency applies
    # (round_trip = charge_eff × discharge_eff, so discharge ≈ sqrt)
    discharge_efficiency = round_trip_efficiency ** 0.5
    effective_kwh = exportable_kwh * discharge_efficiency

    # Filter to eligible slots: rate above threshold, not already ended
    eligible = [
        s for s in upcoming_slots
        if s.rate_inc_vat_pence >= export_threshold_pence
        and s.interval_end > now
    ]

    if not eligible:
        return None

    # Sort by rate descending (greedy: highest value first)
    eligible.sort(key=lambda s: s.rate_inc_vat_pence, reverse=True)

    # Calculate how many slots we need at max comfortable discharge
    max_kwh_per_slot = max_discharge_kw * 0.5  # 30-minute slots
    slots_needed = math.ceil(effective_kwh / max_kwh_per_slot)

    # Take the top N slots by rate
    selected = eligible[:slots_needed]
    selected_count = len(selected)

    # Allocate energy to highest-rate slots first at max power
    remaining = effective_kwh
    allocations = []
    for slot in selected:  # already sorted by rate desc
        alloc_kwh = min(remaining, max_kwh_per_slot)
        allocations.append((slot, alloc_kwh))
        remaining -= alloc_kwh

    # Drop partial slots with < 0.5 kWh — not worth a whole slot
    allocations = [(s, kwh) for s, kwh in allocations if kwh >= 0.5]

    if not allocations:
        return None

    # Build planned slots, sorted by time for easy lookup
    planned = []
    for slot, kwh in sorted(allocations, key=lambda x: x[0].interval_start):
        kw = min(kwh / 0.5, max_discharge_kw)
        planned.append(PlannedSlot(
            interval_start=slot.interval_start,
            interval_end=slot.interval_end,
            rate_pence=slot.rate_inc_vat_pence,
            discharge_kw=round(kw, 3),
            expected_kwh=round(kwh, 4),
        ))

    total_kwh = round(sum(kwh for _, kwh in allocations), 4)
    actual_kw = max(s.discharge_kw for s in planned)

    return ExportPlan(
        created_at=datetime.now(timezone.utc),
        planned_slots=planned,
        total_planned_kwh=total_kwh,
        exportable_kwh=round(exportable_kwh, 4),
        discharge_kw=round(actual_kw, 3),
    )
