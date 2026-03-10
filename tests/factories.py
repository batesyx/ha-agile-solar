"""Test data factories for all domain models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from octopus_export_optimizer.models.ha_state import HaStateSnapshot
from octopus_export_optimizer.models.meter import MeterInterval
from octopus_export_optimizer.models.recommendation import RecommendationInputSnapshot
from octopus_export_optimizer.models.revenue import RevenueInterval
from octopus_export_optimizer.models.tariff import TariffSlot


def make_tariff_slot(
    interval_start: datetime | None = None,
    rate_pence: float = 15.0,
    tariff_type: str = "export",
    product_code: str = "AGILE-OUTGOING-19-05-13",
    provenance: str = "published",
) -> TariffSlot:
    """Create a TariffSlot with sensible defaults."""
    start = interval_start or datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    return TariffSlot(
        interval_start=start,
        interval_end=start + timedelta(minutes=30),
        rate_inc_vat_pence=rate_pence,
        tariff_type=tariff_type,
        product_code=product_code,
        provenance=provenance,
        fetched_at=datetime.now(timezone.utc),
    )


def make_meter_interval(
    interval_start: datetime | None = None,
    kwh: float = 1.5,
    direction: str = "export",
) -> MeterInterval:
    """Create a MeterInterval with sensible defaults."""
    start = interval_start or datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
    return MeterInterval(
        interval_start=start,
        interval_end=start + timedelta(minutes=30),
        kwh=kwh,
        direction=direction,
        fetched_at=datetime.now(timezone.utc),
    )


def make_ha_state(
    battery_soc_pct: float | None = 65.0,
    pv_power_kw: float | None = 3.5,
    feed_in_kw: float | None = 2.0,
    load_power_kw: float | None = 1.5,
    timestamp: datetime | None = None,
) -> HaStateSnapshot:
    """Create an HaStateSnapshot with sensible defaults."""
    return HaStateSnapshot(
        timestamp=timestamp or datetime.now(timezone.utc),
        battery_soc_pct=battery_soc_pct,
        pv_power_kw=pv_power_kw,
        feed_in_kw=feed_in_kw,
        load_power_kw=load_power_kw,
        grid_consumption_kw=0.0,
        battery_charge_kw=0.0,
        battery_discharge_kw=0.0,
    )


def make_recommendation_snapshot(
    current_export_rate: float | None = 15.0,
    best_upcoming_rate: float | None = 20.0,
    battery_soc_pct: float | None = 65.0,
    feed_in_kw: float | None = 2.0,
    upcoming_rates_count: int = 8,
    remaining_generation: float | None = 0.5,
    timestamp: datetime | None = None,
    tariff_data_age_minutes: float | None = None,
) -> RecommendationInputSnapshot:
    """Create a RecommendationInputSnapshot with sensible defaults."""
    now = timestamp or datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    return RecommendationInputSnapshot(
        timestamp=now,
        battery_soc_pct=battery_soc_pct,
        current_export_rate_pence=current_export_rate,
        best_upcoming_rate_pence=best_upcoming_rate,
        best_upcoming_slot_start=now + timedelta(hours=2) if best_upcoming_rate else None,
        upcoming_rates_count=upcoming_rates_count,
        current_import_rate_pence=7.5,
        feed_in_kw=feed_in_kw,
        pv_power_kw=3.5,
        load_power_kw=1.5,
        remaining_generation_heuristic=remaining_generation,
        exportable_battery_kwh=5.0 if battery_soc_pct else None,
        battery_headroom_kwh=3.0 if battery_soc_pct else None,
        tariff_data_age_minutes=tariff_data_age_minutes,
    )
