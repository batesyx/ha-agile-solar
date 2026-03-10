"""Recommendation state and reason code enums."""

from enum import Enum


class RecommendationState(str, Enum):
    """Allowed recommendation states."""

    EXPORT_NOW = "EXPORT_NOW"
    HOLD_BATTERY = "HOLD_BATTERY"
    NORMAL_SELF_CONSUMPTION = "NORMAL_SELF_CONSUMPTION"
    CHARGE_FOR_LATER_EXPORT = "CHARGE_FOR_LATER_EXPORT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ReasonCode(str, Enum):
    """Machine-readable reason codes for recommendations."""

    # Insufficient data reasons
    NO_TARIFF_DATA = "NO_TARIFF_DATA"
    STALE_TARIFF_DATA = "STALE_TARIFF_DATA"
    NO_BATTERY_STATE = "NO_BATTERY_STATE"

    # Export now reasons
    HIGH_EXPORT_RATE = "HIGH_EXPORT_RATE"
    HIGH_RATE_WITH_BATTERY = "HIGH_RATE_WITH_BATTERY"
    HIGH_RATE_SOLAR_EXPORT = "HIGH_RATE_SOLAR_EXPORT"

    # Hold battery reasons
    BETTER_SLOT_COMING = "BETTER_SLOT_COMING"
    BETTER_SLOT_HIGH_SOC = "BETTER_SLOT_HIGH_SOC"

    # Normal self-consumption reasons
    LOW_EXPORT_RATE = "LOW_EXPORT_RATE"
    NO_EXPORT_OPPORTUNITY = "NO_EXPORT_OPPORTUNITY"
    DEFAULT_OPERATION = "DEFAULT_OPERATION"

    # Planned export reasons
    PLANNED_EXPORT = "PLANNED_EXPORT"
    PLANNED_HOLD = "PLANNED_HOLD"

    # Charge for later export reasons
    CHEAP_IMPORT_HIGH_EXPORT_LATER = "CHEAP_IMPORT_HIGH_EXPORT_LATER"
    OVERNIGHT_CHARGE_STRATEGY = "OVERNIGHT_CHARGE_STRATEGY"
