"""Maps recommendation states to Fox ESS inverter work modes."""

from __future__ import annotations

from enum import Enum

from octopus_export_optimizer.recommendation.types import RecommendationState


class WorkMode(str, Enum):
    """Fox ESS inverter work modes (must match HA select entity options exactly)."""

    FEED_IN_FIRST = "Feed-in First"
    SELF_USE = "Self Use"
    BACK_UP = "Back-up"
    FORCE_CHARGE = "Force Charge"
    FORCE_DISCHARGE = "Force Discharge"


_RECOMMENDATION_TO_MODE: dict[RecommendationState, WorkMode | None] = {
    RecommendationState.EXPORT_NOW: WorkMode.FEED_IN_FIRST,
    RecommendationState.HOLD_BATTERY: WorkMode.FEED_IN_FIRST,
    RecommendationState.CHARGE_FOR_LATER_EXPORT: WorkMode.FORCE_CHARGE,
    RecommendationState.NORMAL_SELF_CONSUMPTION: WorkMode.FEED_IN_FIRST,
    RecommendationState.INSUFFICIENT_DATA: None,
}


def map_recommendation_to_mode(state: RecommendationState) -> WorkMode | None:
    """Return the target work mode for a recommendation state.

    Returns None for INSUFFICIENT_DATA (fail-safe: don't change anything).
    """
    return _RECOMMENDATION_TO_MODE.get(state)
