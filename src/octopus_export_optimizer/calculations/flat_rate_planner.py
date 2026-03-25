"""Flat-rate export planner: spreads discharge evenly within time windows.

For flat-rate export tariffs where every slot pays the same,
distributes exportable energy across all available slots within
configured discharge windows at the minimum even discharge power.

Supports multiple windows (e.g. morning bleed-down + evening export)
with independent energy budgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from octopus_export_optimizer.models.export_plan import ExportPlan, PlannedSlot
from octopus_export_optimizer.models.tariff import TariffSlot

UK_TZ = ZoneInfo("Europe/London")


@dataclass
class DischargeWindow:
    """A time window with its own energy budget for discharge."""

    start_hour: float  # Local UK time
    end_hour: float  # Local UK time
    exportable_kwh: float  # Energy to export in this window


def build_flat_rate_plan(
    now: datetime,
    upcoming_slots: list[TariffSlot],
    max_discharge_kw: float,
    battery_capacity_kwh: float,
    round_trip_efficiency: float,
    windows: list[DischargeWindow],
) -> ExportPlan | None:
    """Build a discharge schedule spreading energy evenly within windows.

    Args:
        now: Current UTC time.
        upcoming_slots: All upcoming export tariff slots.
        max_discharge_kw: Maximum comfortable discharge power.
        battery_capacity_kwh: Total battery capacity (for reference).
        round_trip_efficiency: Battery round-trip efficiency (0-1).
        windows: Discharge windows, each with start/end hours and energy budget.

    Returns:
        An ExportPlan if there are eligible slots and energy to export,
        None otherwise.
    """
    if not upcoming_slots or not windows:
        return None

    discharge_efficiency = round_trip_efficiency**0.5
    all_planned: list[PlannedSlot] = []
    total_exportable = 0.0

    for window in windows:
        if window.exportable_kwh <= 0:
            continue

        start_utc = _local_hour_to_utc(now, window.start_hour)
        end_utc = _local_hour_to_utc(now, window.end_hour)

        if now >= end_utc:
            continue

        effective_kwh = window.exportable_kwh * discharge_efficiency

        eligible = [
            s for s in upcoming_slots
            if s.interval_end > now
            and s.interval_start >= start_utc
            and s.interval_start < end_utc
        ]

        if not eligible:
            continue

        eligible.sort(key=lambda s: s.interval_start)
        slot_count = len(eligible)

        even_kwh_per_slot = effective_kwh / slot_count
        even_kw = min(max_discharge_kw, even_kwh_per_slot / 0.5)

        # Drop slots with < 0.1 kWh — not worth inverter switching overhead
        if even_kwh_per_slot < 0.1:
            continue

        for slot in eligible:
            all_planned.append(PlannedSlot(
                interval_start=slot.interval_start,
                interval_end=slot.interval_end,
                rate_pence=slot.rate_inc_vat_pence,
                discharge_kw=round(even_kw, 3),
                expected_kwh=round(even_kwh_per_slot, 4),
            ))

        total_exportable += window.exportable_kwh

    if not all_planned:
        return None

    all_planned.sort(key=lambda s: s.interval_start)
    total_kwh = round(sum(s.expected_kwh for s in all_planned), 4)
    max_kw = max(s.discharge_kw for s in all_planned)

    return ExportPlan(
        created_at=datetime.now(timezone.utc),
        planned_slots=all_planned,
        total_planned_kwh=total_kwh,
        exportable_kwh=round(total_exportable, 4),
        discharge_kw=round(max_kw, 3),
    )


def _local_hour_to_utc(now: datetime, hour: float) -> datetime:
    """Convert a local UK hour to a UTC datetime for today."""
    now_uk = now.astimezone(UK_TZ)
    h = int(hour)
    m = int((hour - h) * 60)
    local_dt = now_uk.replace(hour=h, minute=m, second=0, microsecond=0)
    return local_dt.astimezone(timezone.utc)
