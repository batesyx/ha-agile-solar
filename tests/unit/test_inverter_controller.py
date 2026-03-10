"""Tests for InverterController safety layers and command execution."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from octopus_export_optimizer.config.settings import HaEntityIds, HaSettings, InverterControlSettings
from octopus_export_optimizer.control.inverter_controller import InverterController
from octopus_export_optimizer.control.mode_mapper import WorkMode
from octopus_export_optimizer.control.models import CommandResult
from octopus_export_optimizer.models.recommendation import Recommendation
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from octopus_export_optimizer.storage.command_repo import CommandRepo
from octopus_export_optimizer.storage.database import Database


def _make_recommendation(
    state: RecommendationState = RecommendationState.EXPORT_NOW,
    reason_code: ReasonCode = ReasonCode.HIGH_RATE_WITH_BATTERY,
    target_max_soc: int | None = 90,
    target_discharge_kw: float | None = None,
) -> Recommendation:
    return Recommendation(
        timestamp=datetime.now(timezone.utc),
        state=state,
        reason_code=reason_code,
        explanation="test",
        battery_aware=True,
        input_snapshot_id="test-snap",
        target_max_soc=target_max_soc,
        target_discharge_kw=target_discharge_kw,
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

    def test_insufficient_data_falls_back_to_self_use(self, controller):
        """With default fallback='self_use', INSUFFICIENT_DATA sends Self Use."""
        controller.set_auto_control(True)
        rec = _make_recommendation(state=RecommendationState.INSUFFICIENT_DATA)
        result = controller.execute(rec)
        assert result is not None
        assert result.new_mode == WorkMode.SELF_USE.value
        assert result.success is True

    def test_insufficient_data_noop_when_fallback_none(self):
        """With fallback='none', INSUFFICIENT_DATA does nothing."""
        ha = HaSettings(url="http://localhost:8123", token="test-token")
        settings = InverterControlSettings(
            enabled=True, fallback_on_insufficient_data="none",
        )
        ctrl = InverterController(ha, settings, MagicMock())
        ctrl._client = MagicMock()
        ctrl.set_auto_control(True)
        rec = _make_recommendation(state=RecommendationState.INSUFFICIENT_DATA)
        result = ctrl.execute(rec)
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


class TestForceDischarge:
    def test_discharge_kw_overrides_to_force_discharge(self, controller):
        """target_discharge_kw overrides work mode to Force Discharge."""
        controller.set_auto_control(True)
        rec = _make_recommendation(
            state=RecommendationState.EXPORT_NOW,
            reason_code=ReasonCode.PLANNED_EXPORT,
            target_discharge_kw=4.5,
        )
        result = controller.execute(rec)

        assert result is not None
        assert result.success is True
        assert result.new_mode == WorkMode.FORCE_DISCHARGE.value
        assert result.target_discharge_kw == 4.5

    def test_force_discharge_sends_power_command(self, controller):
        """Force discharge sends both work mode and power level."""
        controller.set_auto_control(True)
        rec = _make_recommendation(
            reason_code=ReasonCode.PLANNED_EXPORT,
            target_discharge_kw=5.0,
        )
        controller.execute(rec)

        # Should have called post for work mode + discharge power + max_soc
        calls = controller._client.post.call_args_list
        urls = [c[0][0] for c in calls]
        assert "/api/services/select/select_option" in urls  # work mode
        assert "/api/services/number/set_value" in urls  # power + soc

    def test_discharge_kw_idempotent(self, controller):
        """Same discharge_kw → no repeat command."""
        controller.set_auto_control(True)
        rec = _make_recommendation(
            reason_code=ReasonCode.PLANNED_EXPORT,
            target_discharge_kw=5.0,
        )
        controller.execute(rec)

        # Wait past rate limit
        controller._last_command_time = datetime.now(timezone.utc) - timedelta(seconds=301)
        result = controller.execute(rec)
        assert result is None  # Idempotent

    def test_discharge_kw_change_sends_new_command(self, controller):
        """Changed discharge_kw triggers a new command."""
        controller.set_auto_control(True)
        rec1 = _make_recommendation(
            reason_code=ReasonCode.PLANNED_EXPORT,
            target_discharge_kw=5.0,
        )
        controller.execute(rec1)

        # Wait past rate limit
        controller._last_command_time = datetime.now(timezone.utc) - timedelta(seconds=301)

        rec2 = _make_recommendation(
            reason_code=ReasonCode.PLANNED_EXPORT,
            target_discharge_kw=3.5,
        )
        result = controller.execute(rec2)
        assert result is not None
        assert result.target_discharge_kw == 3.5

    def test_discharge_kw_recorded_in_result(self, controller):
        """CommandResult includes target_discharge_kw."""
        controller.set_auto_control(True)
        rec = _make_recommendation(
            reason_code=ReasonCode.PLANNED_EXPORT,
            target_discharge_kw=4.0,
        )
        result = controller.execute(rec)

        assert result is not None
        saved = controller.command_repo.save.call_args[0][0]
        assert saved.target_discharge_kw == 4.0


class TestCommandRepoPersistence:
    def test_target_discharge_kw_round_trips(self):
        """target_discharge_kw survives save → get_latest via SQLite."""
        db = Database(":memory:")
        db.connect()
        repo = CommandRepo(db)

        record = CommandResult(
            id="test-001",
            timestamp=datetime.now(timezone.utc),
            previous_mode="Self Use",
            new_mode="Force Discharge",
            target_max_soc=90,
            target_discharge_kw=4.5,
            recommendation_state="EXPORT_NOW",
            reason_code="PLANNED_EXPORT",
            success=True,
        )
        repo.save(record)
        loaded = repo.get_latest()

        assert loaded is not None
        assert loaded.target_discharge_kw == 4.5
        assert loaded.new_mode == "Force Discharge"
        db.close()

    def test_target_discharge_kw_none_round_trips(self):
        """None target_discharge_kw persists correctly."""
        db = Database(":memory:")
        db.connect()
        repo = CommandRepo(db)

        record = CommandResult(
            id="test-002",
            timestamp=datetime.now(timezone.utc),
            previous_mode=None,
            new_mode="Self Use",
            target_max_soc=90,
            target_discharge_kw=None,
            recommendation_state="HOLD_BATTERY",
            reason_code="BETTER_SLOT_COMING",
            success=True,
        )
        repo.save(record)
        loaded = repo.get_latest()

        assert loaded is not None
        assert loaded.target_discharge_kw is None
        db.close()
