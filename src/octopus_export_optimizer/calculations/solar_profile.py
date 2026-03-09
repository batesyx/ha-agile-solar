"""Multi-orientation solar generation heuristic.

Provides a simple time-of-day generation estimate for the
multi-orientation array, used to inform hold/export decisions.

This is intentionally approximate in Phase 1 — it uses basic
solar geometry rather than weather forecasts.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from octopus_export_optimizer.config.settings import PanelArray, SolarSettings


class SolarProfile:
    """Heuristic generation curve for a multi-orientation solar array.

    Models approximate clear-sky output based on:
    - Solar elevation and azimuth for the datetime
    - Each panel array's orientation and capacity
    - Cosine of incidence angle between sun and panel
    """

    def __init__(self, settings: SolarSettings) -> None:
        self.panels = settings.panels
        self.latitude = math.radians(settings.latitude)
        self.longitude = settings.longitude
        self.total_kwp = sum(p.kwp for p in self.panels)

    def estimated_generation_kw(
        self,
        dt: datetime,
        cloud_factor: float = 1.0,
    ) -> float:
        """Estimate total generation at a given time.

        Args:
            dt: UTC datetime.
            cloud_factor: 0.0 (overcast) to 1.0 (clear sky). Default clear.

        Returns:
            Estimated generation in kW.
        """
        elevation, azimuth = self._solar_position(dt)
        if elevation <= 0:
            return 0.0

        total_kw = 0.0
        for panel in self.panels:
            output = self._panel_output(panel, elevation, azimuth)
            total_kw += output

        return total_kw * cloud_factor

    def remaining_generation_factor(self, dt: datetime) -> float:
        """Heuristic estimate of remaining generation opportunity today.

        Returns a value from 0.0 (no more generation expected) to 1.0
        (full day of generation remaining). This accounts for the
        multi-orientation array extending the generation window.

        Used to influence hold/export decisions:
        - High value → prefer holding battery headroom for incoming solar
        - Low value → less reason to hold, favour export if rate is good
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        hour_utc = dt.hour + dt.minute / 60.0

        # Estimate sunset hour (approximate for UK, varies by season)
        day_of_year = dt.timetuple().tm_yday
        # Simple sinusoidal model: sunset ~16:00 in winter, ~21:30 in summer (UTC)
        sunset_hour = 18.75 + 2.75 * math.sin(
            2 * math.pi * (day_of_year - 80) / 365
        )
        # Sunrise approximately symmetric
        sunrise_hour = 12.0 - (sunset_hour - 12.0)

        if hour_utc >= sunset_hour or hour_utc < sunrise_hour:
            return 0.0

        day_length = sunset_hour - sunrise_hour
        hours_elapsed = hour_utc - sunrise_hour
        hours_remaining = sunset_hour - hour_utc

        # Base factor: fraction of generation day remaining
        base_factor = hours_remaining / day_length

        # Multi-orientation bonus: the array continues producing
        # beyond what a south-only system would. West panels extend
        # afternoon generation. Give a small bonus in afternoon.
        west_kwp = sum(p.kwp for p in self.panels if 225 <= p.orientation <= 315)
        if west_kwp > 0 and hours_elapsed > day_length * 0.5:
            # In the afternoon, west panels add value
            west_bonus = 0.1 * (west_kwp / self.total_kwp)
            base_factor = min(1.0, base_factor + west_bonus)

        return max(0.0, min(1.0, base_factor))

    def _panel_output(
        self,
        panel: PanelArray,
        sun_elevation: float,
        sun_azimuth: float,
    ) -> float:
        """Calculate output for a single panel array.

        Uses cosine of incidence angle between sun direction
        and panel normal to estimate relative output.
        """
        panel_azimuth_rad = math.radians(panel.orientation)
        panel_tilt_rad = math.radians(panel.tilt)
        sun_azimuth_rad = math.radians(sun_azimuth)
        sun_elevation_rad = math.radians(sun_elevation)

        # Cosine of incidence angle on a tilted surface
        cos_incidence = (
            math.sin(sun_elevation_rad) * math.cos(panel_tilt_rad)
            + math.cos(sun_elevation_rad)
            * math.sin(panel_tilt_rad)
            * math.cos(sun_azimuth_rad - panel_azimuth_rad)
        )

        if cos_incidence <= 0:
            return 0.0

        # Scale by panel capacity and atmospheric clearness
        # Simple air mass correction
        air_mass = 1.0 / max(math.sin(sun_elevation_rad), 0.05)
        clearness = max(0.0, 1.0 - 0.1 * (air_mass - 1.0))

        return panel.kwp * cos_incidence * clearness

    def _solar_position(self, dt: datetime) -> tuple[float, float]:
        """Calculate approximate solar elevation and azimuth.

        Returns (elevation_degrees, azimuth_degrees).
        Azimuth: 0=North, 90=East, 180=South, 270=West.
        """
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        day_of_year = dt.timetuple().tm_yday
        hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0

        # Solar declination (Spencer, 1971)
        gamma = 2 * math.pi * (day_of_year - 1) / 365
        declination = (
            0.006918
            - 0.399912 * math.cos(gamma)
            + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma)
        )

        # Equation of time (minutes)
        eqtime = 229.18 * (
            0.000075
            + 0.001868 * math.cos(gamma)
            - 0.032077 * math.sin(gamma)
            - 0.014615 * math.cos(2 * gamma)
            - 0.04089 * math.sin(2 * gamma)
        )

        # Solar hour angle
        time_offset = eqtime + 4 * self.longitude
        true_solar_time = hour * 60 + time_offset
        hour_angle = math.radians((true_solar_time / 4) - 180)

        # Solar elevation
        sin_elevation = (
            math.sin(self.latitude) * math.sin(declination)
            + math.cos(self.latitude)
            * math.cos(declination)
            * math.cos(hour_angle)
        )
        elevation = math.degrees(math.asin(max(-1, min(1, sin_elevation))))

        # Solar azimuth
        cos_azimuth = (
            math.sin(declination) - math.sin(self.latitude) * sin_elevation
        ) / max(math.cos(self.latitude) * math.cos(math.radians(elevation)), 0.001)
        cos_azimuth = max(-1, min(1, cos_azimuth))
        azimuth = math.degrees(math.acos(cos_azimuth))

        if hour_angle > 0:
            azimuth = 360 - azimuth

        return elevation, azimuth
