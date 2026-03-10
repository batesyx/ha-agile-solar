"""Tests for InverterController safety layers and command execution."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from octopus_export_optimizer.config.settings import HaEntityIds, HaSettings, InverterControlSettings
from octopus_export_optimizer.control.inverter_controller import InverterController
from octopus_export_optimizer.control.mode_mapper import WorkMode
from octopus_export_optimizer.models.recommendation import Recommendation
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState


def _make_recommendation(
    state: RecommendationState = RecommendationState.EXPORT_NOW,
    reason_code: ReasonCode = ReasonCode.HIGH_RATE_WITH_BATTERY,
    target_max_soc: int | None = 90,
) -> Recommendation:
    return Recommendation(
        timestamp=datetime.now(timezone.utc),
        state=state,
        reason_code=reason_code,
        explanation="test",
        battery_aware=True,
        input_snapshot_id="test-snap",
        target_max_soc=target_max_soc,
    )


@pytest.fixture
def controller():
    ha = HaSettings(
        url="http://localhost:8123",
        token="test-token",
    )
    settings = InverterControlSettings(enabled=True, min_command_interval_seconds=300)
    repo = MagicMock()
    ctrl = InverterController(ha, settings, repo)
    ctrl._client = MagicMock()
    ctrl._client.post.return_value = MagicMock(status_code=200)
    ctrl._client.post.return_value.raise_for_status = MagicMock()
    return ctrl


class TestSafetyLayers:
    def test_disabled_config_returns_none(self):
        ha = HaSettings(url="http://localhost:8123", token="test-token")
        settings = InverterControlSettings(enabled=False)
        ctrl = InverterController(ha, settings, MagicMock())
        result = ctrl.execute(_make_recommendation())
        assert result is None

    def test_kill_switch_off_returns_none(self, controller):
        """Auto control disabled (default) — no commands sent."""
        assert controller.auto_control_enabled is False
        result = controller.execute(_make_recommendation())
        assert result is None

    def test_insufficient_data_never_acted_on(self, controller):
        controller.set_auto_control(True)
        rec = _make_recommendation(state=RecommendationState.INSUFFICIENT_DATA)
        result = controller.execute(rec)
        assert result is None

    def test_rate_limited(self, controller):
        """Second command within interval is blocked."""
        controller.set_auto_control(True)
        rec = _make_recommendation()
        result1 = controller.execute(rec)
        assert result1 is not None
        assert result1.success is True

        # Change target so it's not idempotent
        rec2 = _make_recommendation(target_max_soc=95)
        result2 = controller.execute(rec2)
        assert result2 is None  # Rate limited

    def test_idempotent_no_op(self, controller):
        """Same mode + same SoC → no command sent."""
        controller.set_auto_control(True)
        rec = _make_recommendation()
        controller.execute(rec)

        # Wait past rate limit
        controller._last_command_time = datetime.now(timezone.utc) - timedelta(seconds=301)
        result = controller.execute(rec)
        assert result is None  # Idempotent


class TestCommandExecution:
    def test_mode_change_sends_http(self, controller):
        controller.set_auto_control(True)
        rec = _make_recommendation(state=RecommendationState.EXPORT_NOW)
        result = controller.execute(rec)

        assert result is not None
        assert result.success is True
        assert result.new_mode == WorkMode.FEED_IN_FIRST.value
        controller._client.post.assert_called()

    def test_max_soc_clamped_low(self, controller):
        """Max SoC below 10 gets clamped to 10."""
        controller.set_auto_control(True)
        rec = _make_recommendation(target_max_soc=5)
        result = controller.execute(rec)
        assert result is not None
        assert result.target_max_soc == 10

    def test_max_soc_clamped_high(self, controller):
        """Max SoC above 100 gets clamped to 100."""
        controller.set_auto_control(True)
        rec = _make_recommendation(target_max_soc=150)
        result = controller.execute(rec)
        assert result is not None
        assert result.target_max_soc == 100

    def test_http_failure_records_error(self, controller):
        controller.set_auto_control(True)
        controller._client.post.side_effect = Exception("Connection refused")

        rec = _make_recommendation()
        result = controller.execute(rec)

        assert result is not None
        assert result.success is False
        assert "Connection refused" in result.error

    def test_failed_command_does_not_update_state(self, controller):
        controller.set_auto_control(True)
        controller._client.post.side_effect = Exception("timeout")

        rec = _make_recommendation()
        controller.execute(rec)

        assert controller._last_commanded_mode is None
        assert controller._last_command_time is None

    def test_successful_command_updates_state(self, controller):
        controller.set_auto_control(True)
        rec = _make_recommendation()
        controller.execute(rec)

        assert controller._last_commanded_mode == WorkMode.FEED_IN_FIRST
        assert controller._last_command_time is not None

    def test_command_result_saved_to_repo(self, controller):
        controller.set_auto_control(True)
        rec = _make_recommendation()
        controller.execute(rec)

        controller.command_repo.save.assert_called_once()
