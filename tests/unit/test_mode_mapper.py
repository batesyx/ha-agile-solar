"""Tests for recommendation-to-inverter-mode mapping."""

import pytest

from octopus_export_optimizer.control.mode_mapper import WorkMode, map_recommendation_to_mode
from octopus_export_optimizer.recommendation.types import RecommendationState


@pytest.mark.parametrize(
    "state, expected_mode",
    [
        (RecommendationState.EXPORT_NOW, WorkMode.FEED_IN_FIRST),
        (RecommendationState.HOLD_BATTERY, WorkMode.SELF_USE),
        (RecommendationState.CHARGE_FOR_LATER_EXPORT, WorkMode.FORCE_CHARGE),
        (RecommendationState.NORMAL_SELF_CONSUMPTION, WorkMode.SELF_USE),
        (RecommendationState.INSUFFICIENT_DATA, None),
    ],
)
def test_all_states_mapped(state: RecommendationState, expected_mode: WorkMode | None):
    assert map_recommendation_to_mode(state) == expected_mode


def test_work_mode_values_match_ha_options():
    """Verify WorkMode string values match the exact HA entity options."""
    assert WorkMode.FEED_IN_FIRST.value == "Feed-in First"
    assert WorkMode.SELF_USE.value == "Self Use"
    assert WorkMode.FORCE_CHARGE.value == "Force Charge"
    assert WorkMode.BACK_UP.value == "Back-up"
    assert WorkMode.FORCE_DISCHARGE.value == "Force Discharge"
