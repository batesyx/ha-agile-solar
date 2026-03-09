"""Tests for application settings."""

from datetime import date

import pytest

from octopus_export_optimizer.config.settings import (
    FlatRateConfig,
    ThresholdSettings,
)


class TestFlatRateForDate:
    def test_default_rate(self):
        thresholds = ThresholdSettings()
        assert thresholds.get_flat_rate_for_date(date(2026, 1, 1)) == 12.0

    def test_multiple_rates(self):
        thresholds = ThresholdSettings(
            flat_export_rates=[
                FlatRateConfig(
                    rate_pence=10.0,
                    effective_from=date(2024, 1, 1),
                    effective_to=date(2024, 12, 31),
                ),
                FlatRateConfig(
                    rate_pence=12.0,
                    effective_from=date(2025, 1, 1),
                    effective_to=date(2025, 6, 30),
                ),
                FlatRateConfig(
                    rate_pence=15.0,
                    effective_from=date(2025, 7, 1),
                ),
            ]
        )

        assert thresholds.get_flat_rate_for_date(date(2024, 6, 15)) == 10.0
        assert thresholds.get_flat_rate_for_date(date(2025, 3, 1)) == 12.0
        assert thresholds.get_flat_rate_for_date(date(2025, 9, 1)) == 15.0
        assert thresholds.get_flat_rate_for_date(date(2026, 1, 1)) == 15.0

    def test_fallback_to_earliest(self):
        thresholds = ThresholdSettings(
            flat_export_rates=[
                FlatRateConfig(
                    rate_pence=8.0,
                    effective_from=date(2025, 6, 1),
                ),
            ]
        )
        # Before any rate's effective_from
        assert thresholds.get_flat_rate_for_date(date(2024, 1, 1)) == 8.0


class TestThresholdDefaults:
    def test_defaults_are_sensible(self):
        t = ThresholdSettings()
        assert t.export_now_threshold_pence == 15.0
        assert t.better_slot_delta_pence == 3.0
        assert t.look_ahead_hours == 4.0
        assert t.reserve_soc_floor == 0.20
        assert t.minimum_soc_for_export == 0.35
