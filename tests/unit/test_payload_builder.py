"""Tests for MQTT payload builder."""

import json
from datetime import datetime, timezone

from octopus_export_optimizer.publishing.payload_builder import PayloadBuilder
from octopus_export_optimizer.recommendation.types import ReasonCode, RecommendationState
from tests.factories import make_tariff_slot


class TestRatePayload:
    def test_with_slot(self):
        slot = make_tariff_slot(rate_pence=22.5)
        payload = json.loads(PayloadBuilder.rate_payload(slot))
        assert payload["rate"] == 22.5
        assert payload["valid_from"] is not None

    def test_none_slot(self):
        payload = json.loads(PayloadBuilder.rate_payload(None))
        assert payload["rate"] is None


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
