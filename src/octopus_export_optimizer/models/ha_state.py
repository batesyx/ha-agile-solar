"""Home Assistant state snapshot domain model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HaStateSnapshot(BaseModel):
    """Point-in-time snapshot of Home Assistant device state.

    This is operational state for real-time decisions, NOT
    settlement-grade data for revenue calculations.
    """

    timestamp: datetime  # UTC
    battery_soc_pct: float | None = None
    pv_power_kw: float | None = None
    feed_in_kw: float | None = None
    load_power_kw: float | None = None
    grid_consumption_kw: float | None = None
    battery_charge_kw: float | None = None
    battery_discharge_kw: float | None = None
    work_mode: str | None = None
    max_soc: float | None = None
    min_soc: float | None = None
    force_charge_power_kw: float | None = None
    force_discharge_power_kw: float | None = None
    battery_charge_today_kwh: float | None = None

    @property
    def has_battery_data(self) -> bool:
        """Whether we have enough battery data for battery-aware decisions."""
        return self.battery_soc_pct is not None

    @property
    def has_power_data(self) -> bool:
        """Whether we have enough power data for operational decisions."""
        return self.pv_power_kw is not None or self.feed_in_kw is not None

    @property
    def net_battery_power_kw(self) -> float | None:
        """Net battery power: positive = discharging, negative = charging."""
        if self.battery_discharge_kw is not None and self.battery_charge_kw is not None:
            return self.battery_discharge_kw - self.battery_charge_kw
        if self.battery_discharge_kw is not None:
            return self.battery_discharge_kw
        if self.battery_charge_kw is not None:
            return -self.battery_charge_kw
        return None
