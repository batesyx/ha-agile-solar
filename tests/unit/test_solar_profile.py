"""Tests for the solar profile heuristic."""

from datetime import datetime, timezone

import pytest

from octopus_export_optimizer.calculations.solar_profile import SolarProfile
from octopus_export_optimizer.config.settings import SolarSettings


@pytest.fixture
def profile():
    return SolarProfile(SolarSettings())


class TestEstimatedGeneration:
    def test_zero_at_night(self, profile):
        midnight = datetime(2026, 6, 21, 0, 0, tzinfo=timezone.utc)
        assert profile.estimated_generation_kw(midnight) == 0.0

    def test_positive_at_noon_summer(self, profile):
        noon = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
        output = profile.estimated_generation_kw(noon)
        assert output > 0

    def test_cloud_factor_reduces_output(self, profile):
        noon = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
        clear = profile.estimated_generation_kw(noon, cloud_factor=1.0)
        cloudy = profile.estimated_generation_kw(noon, cloud_factor=0.3)
        assert cloudy < clear

    def test_winter_lower_than_summer(self, profile):
        summer_noon = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
        winter_noon = datetime(2026, 12, 21, 12, 0, tzinfo=timezone.utc)
        summer = profile.estimated_generation_kw(summer_noon)
        winter = profile.estimated_generation_kw(winter_noon)
        assert winter < summer


class TestRemainingGenerationFactor:
    def test_high_in_morning(self, profile):
        morning = datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc)
        factor = profile.remaining_generation_factor(morning)
        assert factor > 0.5

    def test_low_in_evening(self, profile):
        evening = datetime(2026, 6, 21, 19, 0, tzinfo=timezone.utc)
        factor = profile.remaining_generation_factor(evening)
        assert factor < 0.3

    def test_zero_at_night(self, profile):
        night = datetime(2026, 6, 21, 23, 0, tzinfo=timezone.utc)
        factor = profile.remaining_generation_factor(night)
        assert factor == 0.0

    def test_monotonically_decreasing_through_day(self, profile):
        factors = []
        for hour in range(8, 20):
            dt = datetime(2026, 6, 21, hour, 0, tzinfo=timezone.utc)
            factors.append(profile.remaining_generation_factor(dt))

        for i in range(1, len(factors)):
            assert factors[i] <= factors[i - 1] + 0.01  # allow tiny float tolerance
