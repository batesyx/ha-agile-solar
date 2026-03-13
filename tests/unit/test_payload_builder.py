"""Tests for MQTT payload builder."""

import json
from datetime import datetime, timezone

from octopus_export_optimizer.publishing.payload_builder import PayloadBuilder
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from tests.factories import make_tariff_slot


class TestRatePayload:
    def test_with_slot(self):
        slot = make_tariff_slot(rate_pence=22.5)
        payload = PayloadBuilder.rate_payload(slot)
        assert payload == "22.50"

    def test_none_slot(self):
        payload = PayloadBuilder.rate_payload(None)
        assert payload == ""


class TestRecommendationPayloads:
    def test_state_payload(self):
        from octopus_export_optimizer.models.recommendation import Recommendation

        rec = Recommendation(
            timestamp=datetime.now(timezone.utc),
            state=RecommendationState.EXPORT_NOW,
            reason_code=ReasonCode.HIGH_EXPORT_RATE,
            explanation="Test explanation",
            battery_aware=False,
            input_snapshot_id="test-id",
        )
        assert PayloadBuilder.recommendation_state_payload(rec) == "EXPORT_NOW"
        assert PayloadBuilder.recommendation_mode_payload(rec) == "tariff-only"

    def test_none_recommendation(self):
        assert PayloadBuilder.recommendation_state_payload(None) == "UNKNOWN"
        assert "No recommendation" in PayloadBuilder.recommendation_explanation_payload(None)


class TestRateSchedulePayload:
    def test_empty_slots(self):
        payload = json.loads(PayloadBuilder.rate_schedule_payload([]))
        assert payload == {"rates": [], "count": 0}

    def test_planned_flag_marks_targeted_slots(self):
        s1 = make_tariff_slot(rate_pence=20.0)
        s2 = make_tariff_slot(
            interval_start=datetime(2026, 3, 9, 12, 30, tzinfo=timezone.utc),
            rate_pence=25.0,
        )
        planned = {s1.interval_start.isoformat(): (5.0, 2.5)}
        payload = json.loads(
            PayloadBuilder.rate_schedule_payload([s1, s2], planned_starts=planned)
        )
        assert payload["rates"][0]["planned"] is True
        assert payload["rates"][0]["discharge_kw"] == 5.0
        assert payload["rates"][0]["expected_kwh"] == 2.5
        assert payload["rates"][1]["planned"] is False
        assert payload["rates"][1]["discharge_kw"] is None
        assert payload["rates"][1]["expected_kwh"] is None

    def test_no_planned_starts_all_false(self):
        s1 = make_tariff_slot(rate_pence=20.0)
        payload = json.loads(PayloadBuilder.rate_schedule_payload([s1]))
        assert payload["rates"][0]["planned"] is False

    def test_planned_starts_none_all_false(self):
        s1 = make_tariff_slot(rate_pence=20.0)
        payload = json.loads(
            PayloadBuilder.rate_schedule_payload([s1], planned_starts=None)
        )
        assert payload["rates"][0]["planned"] is False
