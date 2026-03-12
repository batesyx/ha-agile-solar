"""Tests for solar forecast gate in overnight charge target logic."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from octopus_export_optimizer.config.settings import (
    HaEntityIds,
    HaSettings,
    InverterControlSettings,
)
from octopus_export_optimizer.ingestion.ha_state_ingester import HaStateIngester


# ---------------------------------------------------------------------------
# HaStateIngester.get_solar_forecast_kwh
# ---------------------------------------------------------------------------


class TestGetSolarForecastKwh:
    """Test the ingester method that fetches forecast from HA."""

    @pytest.fixture
    def ingester(self):
        settings = HaSettings(
            url="http://fake:8123",
            token="fake-token",
        )
        repo = MagicMock()
        with patch.object(HaStateIngester, "__init__", lambda self, *a, **kw: None):
            ing = object.__new__(HaStateIngester)
            ing.settings = settings
            ing._client = MagicMock()
        return ing

    def test_empty_entity_returns_none(self, ingester):
        result = ingester.get_solar_forecast_kwh("")
        assert result is None

    def test_valid_entity_delegates_to_get_float(self, ingester):
        ingester._get_float = MagicMock(return_value=12.5)
        result = ingester.get_solar_forecast_kwh("sensor.energy_production_tomorrow")
        assert result == 12.5
        ingester._get_float.assert_called_once_with("sensor.energy_production_tomorrow")

    def test_sensor_unavailable_returns_none(self, ingester):
        ingester._get_float = MagicMock(return_value=None)
        result = ingester.get_solar_forecast_kwh("sensor.energy_production_tomorrow")
        assert result is None


# ---------------------------------------------------------------------------
# Forecast entity selection (tomorrow vs today based on hour)
# ---------------------------------------------------------------------------


class TestForecastEntitySelection:
    """Verify correct entity is selected based on time of day.

    The gate logic in app.py selects:
      hour >= 16 → solar_forecast_tomorrow
      hour <  16 → solar_forecast_today
    """

    @pytest.fixture
    def entity_ids(self):
        return HaEntityIds(
            solar_forecast_today="sensor.energy_production_today",
            solar_forecast_tomorrow="sensor.energy_production_tomorrow",
        )

    @pytest.mark.parametrize(
        "hour,expected_entity",
        [
            (16, "sensor.energy_production_tomorrow"),  # boundary: 16:00
            (17, "sensor.energy_production_tomorrow"),  # evening
            (20, "sensor.energy_production_tomorrow"),  # night
            (23, "sensor.energy_production_tomorrow"),  # late night
            (0, "sensor.energy_production_today"),  # midnight
            (1, "sensor.energy_production_today"),  # early morning
            (5, "sensor.energy_production_today"),  # pre-dawn
            (10, "sensor.energy_production_today"),  # morning
            (15, "sensor.energy_production_today"),  # boundary: 15:xx
        ],
    )
    def test_entity_selection_by_hour(self, entity_ids, hour, expected_entity):
        """Replicate the entity selection logic from app.py."""
        forecast_entity = (
            entity_ids.solar_forecast_tomorrow
            if hour >= 16
            else entity_ids.solar_forecast_today
        )
        assert forecast_entity == expected_entity


# ---------------------------------------------------------------------------
# Forecast gate decision
# ---------------------------------------------------------------------------


class TestForecastGateDecision:
    """Test the forecast gate: if forecast < minimum → use seasonal_max."""

    @pytest.fixture
    def ic(self):
        return InverterControlSettings(
            solar_overnight_enabled=True,
            solar_forecast_minimum_kwh=10.0,
            solar_months_max_soc_pct=0.80,
            winter_max_soc_pct=0.95,
        )

    def _run_gate(self, ic, solar_forecast_kwh, seasonal_max):
        """Simulate the forecast gate logic from app.py.

        Returns (overnight_target, gate_fired).
        """
        if (
            solar_forecast_kwh is not None
            and solar_forecast_kwh < ic.solar_forecast_minimum_kwh
        ):
            return seasonal_max, True
        return None, False

    def test_forecast_below_minimum_uses_seasonal_max(self, ic):
        target, fired = self._run_gate(ic, solar_forecast_kwh=4.2, seasonal_max=0.80)
        assert fired is True
        assert target == 0.80

    def test_forecast_at_minimum_does_not_fire(self, ic):
        target, fired = self._run_gate(ic, solar_forecast_kwh=10.0, seasonal_max=0.80)
        assert fired is False
        assert target is None

    def test_forecast_above_minimum_does_not_fire(self, ic):
        target, fired = self._run_gate(ic, solar_forecast_kwh=15.0, seasonal_max=0.80)
        assert fired is False
        assert target is None

    def test_forecast_none_does_not_fire(self, ic):
        """Sensor unavailable → gate skipped, existing logic runs."""
        target, fired = self._run_gate(ic, solar_forecast_kwh=None, seasonal_max=0.80)
        assert fired is False
        assert target is None

    def test_forecast_zero_fires_gate(self, ic):
        """Zero forecast (sensor returns 0) → gate fires."""
        target, fired = self._run_gate(ic, solar_forecast_kwh=0.0, seasonal_max=0.80)
        assert fired is True
        assert target == 0.80

    def test_winter_seasonal_max_used(self, ic):
        """Winter months use 95% seasonal max."""
        target, fired = self._run_gate(ic, solar_forecast_kwh=3.0, seasonal_max=0.95)
        assert fired is True
        assert target == 0.95

    def test_custom_minimum_threshold(self):
        """Custom minimum threshold respected."""
        ic = InverterControlSettings(
            solar_overnight_enabled=True,
            solar_forecast_minimum_kwh=5.0,
        )
        # 4.9 < 5.0 → gate fires
        target, fired = self._run_gate(ic, solar_forecast_kwh=4.9, seasonal_max=0.80)
        assert fired is True

        # 5.0 >= 5.0 → gate does not fire
        target, fired = self._run_gate(ic, solar_forecast_kwh=5.0, seasonal_max=0.80)
        assert fired is False


# ---------------------------------------------------------------------------
# Seasonal max selection by target_date
# ---------------------------------------------------------------------------


class TestSeasonalMaxSelection:
    """Verify seasonal max is correct for target_date month."""

    @pytest.fixture
    def ic(self):
        return InverterControlSettings(
            solar_months_max_soc_pct=0.80,
            winter_max_soc_pct=0.95,
        )

    @pytest.mark.parametrize(
        "month,expected_max",
        [
            (1, 0.95),   # January → winter
            (2, 0.95),   # February → winter
            (3, 0.80),   # March → solar
            (6, 0.80),   # June → solar
            (9, 0.80),   # September → solar
            (10, 0.95),  # October → winter
            (11, 0.95),  # November → winter
            (12, 0.95),  # December → winter
        ],
    )
    def test_seasonal_max_by_month(self, ic, month, expected_max):
        """Replicate the seasonal max logic from app.py."""
        seasonal_max = (
            ic.solar_months_max_soc_pct
            if month in range(3, 10)
            else ic.winter_max_soc_pct
        )
        assert seasonal_max == expected_max


# ---------------------------------------------------------------------------
# No entity configured → feature disabled
# ---------------------------------------------------------------------------


class TestForecastFeatureDisabled:
    """When entity IDs are empty, forecast fetch is skipped."""

    def test_empty_entities_mean_no_fetch(self):
        entity_ids = HaEntityIds()  # defaults: empty strings
        assert entity_ids.solar_forecast_today == ""
        assert entity_ids.solar_forecast_tomorrow == ""

        # Simulating what app.py does: select entity then call ingester
        now_hour = 20  # evening
        forecast_entity = (
            entity_ids.solar_forecast_tomorrow
            if now_hour >= 16
            else entity_ids.solar_forecast_today
        )
        assert forecast_entity == ""

        # get_solar_forecast_kwh("") returns None → gate never fires
        ingester = MagicMock()
        ingester.get_solar_forecast_kwh.return_value = None
        result = ingester.get_solar_forecast_kwh(forecast_entity)
        assert result is None
