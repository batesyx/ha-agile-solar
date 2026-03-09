"""Recommendation domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState


class RecommendationInputSnapshot(BaseModel):
    """Exact inputs used to produce a recommendation.

    Stored alongside every recommendation for auditability
    and reproducibility.
    """

    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex)
    timestamp: datetime
    battery_soc_pct: float | None = None
    current_export_rate_pence: float | None = None
    best_upcoming_rate_pence: float | None = None
    best_upcoming_slot_start: datetime | None = None
    upcoming_rates_count: int = 0
    current_import_rate_pence: float | None = None
    solar_estimate_kw: float | None = None
    feed_in_kw: float | None = None
    pv_power_kw: float | None = None
    load_power_kw: float | None = None
    battery_charge_kw: float | None = None
    battery_discharge_kw: float | None = None
    remaining_generation_heuristic: float | None = None
    exportable_battery_kwh: float | None = None
    battery_headroom_kwh: float | None = None


class Recommendation(BaseModel):
    """A generated recommendation with full context.

    Every recommendation is deterministic and reproducible
    from its input snapshot.
    """

    timestamp: datetime
    state: RecommendationState
    reason_code: ReasonCode
    explanation: str
    battery_aware: bool
    valid_until: datetime | None = None
    input_snapshot_id: str
