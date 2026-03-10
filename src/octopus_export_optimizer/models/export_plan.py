"""Export plan domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PlannedSlot(BaseModel):
    """A single planned discharge slot in the export schedule."""

    interval_start: datetime
    interval_end: datetime
    rate_pence: float
    discharge_kw: float
    expected_kwh: float  # discharge_kw × 0.5 hours


class ExportPlan(BaseModel):
    """A complete discharge schedule across multiple half-hour slots.

    Built by the export planner, consumed by PlannedExportRule.
    Recalculated every 60s — not persisted.
    """

    created_at: datetime
    planned_slots: list[PlannedSlot]  # sorted by interval_start
    total_planned_kwh: float
    exportable_kwh: float  # budget at plan creation
    discharge_kw: float  # uniform rate across all slots

    def get_current_slot(self, now: datetime) -> PlannedSlot | None:
        """Return the planned slot covering the current time, or None."""
        for slot in self.planned_slots:
            if slot.interval_start <= now < slot.interval_end:
                return slot
        return None

    def get_next_slot(self, now: datetime) -> PlannedSlot | None:
        """Return the next future planned slot, or None."""
        for slot in self.planned_slots:
            if slot.interval_start > now:
                return slot
        return None
