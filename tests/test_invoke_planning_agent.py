"""Unit tests for the invoke_planning_agent Lambda handler."""

import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.invoke_planning_agent.handler import handler


class TestHandlerValidation:
    """Tests for input validation in the handler."""

    def test_missing_shipment_id_returns_400(self):
        """Handler returns 400 when shipment_id is missing."""
        event = {"origin": "Chicago, IL", "destination": "NYC", "weight_kg": 100}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "shipment_id" in str(result["body"]["error"])

    def test_missing_origin_returns_400(self):
        """Handler returns 400 when origin is missing."""
        event = {"shipment_id": "s-001", "destination": "NYC", "weight_kg": 100}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "origin" in str(result["body"]["error"])

    def test_missing_destination_returns_400(self):
        """Handler returns 400 when destination is missing."""
        event = {"shipment_id": "s-001", "origin": "Chicago, IL", "weight_kg": 100}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "destination" in str(result["body"]["error"])

    def test_missing_weight_kg_returns_400(self):
        """Handler returns 400 when weight_kg is missing."""
        event = {"shipment_id": "s-001", "origin": "Chicago, IL", "destination": "NYC"}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "weight_kg" in str(result["body"]["error"])

    def test_multiple_missing_fields_returns_400(self):
        """Handler returns 400 listing all missing fields."""
        result = handler({}, None)
        assert result["statusCode"] == 400
        error_msg = str(result["body"]["error"])
        assert "shipment_id" in error_msg
        assert "origin" in error_msg
        assert "destination" in error_msg
        assert "weight_kg" in error_msg


class TestHandlerHappyPath:
    """Tests for the successful Planning Agent invocation flow."""

    @patch("lambdas.invoke_planning_agent.handler.publish_event")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_returns_route_from_agent(self, mock_agent, mock_publish):
        """Handler returns the route result from the Planning Agent."""
        mock_agent.return_value = {
            "route": {
                "stops": ["Chicago, IL", "Indianapolis, IN", "New York, NY"],
                "distance_km": 1280.0,
                "transit_hours": 14.5,
            },
            "confidence": 0.85,
            "model_tier": "SLM",
            "model_id": "us.amazon.nova-lite-v1:0",
            "latency_ms": 1200,
        }

        event = {
            "shipment_id": "ship-001",
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": 500,
        }
        result = handler(event, None)

        assert result["shipment_id"] == "ship-001"
        assert result["route"]["stops"] == ["Chicago, IL", "Indianapolis, IN", "New York, NY"]
        assert result["route"]["distance_km"] == 1280.0
        assert result["route"]["transit_hours"] == 14.5
        assert result["confidence"] == 0.85
        assert result["model_tier"] == "SLM"

    @patch("lambdas.invoke_planning_agent.handler.publish_event")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_passes_correct_payload_to_agent(self, mock_agent, mock_publish):
        """Handler constructs the correct payload for the Planning Agent."""
        mock_agent.return_value = {
            "route": {"stops": [], "distance_km": 0, "transit_hours": 0},
            "confidence": 0.80,
            "model_tier": "SLM",
            "model_id": "us.amazon.nova-lite-v1:0",
            "latency_ms": 800,
        }

        event = {
            "shipment_id": "ship-002",
            "origin": "Seattle, WA",
            "destination": "Denver, CO",
            "weight_kg": 250.5,
        }
        handler(event, None)

        mock_agent.assert_called_once_with({
            "origin": "Seattle, WA",
            "destination": "Denver, CO",
            "weight_kg": 250.5,
            "shipment_id": "ship-002",
        })

    @patch("lambdas.invoke_planning_agent.handler.publish_event")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_returns_llm_model_tier_when_escalated(self, mock_agent, mock_publish):
        """Handler returns LLM model_tier when agent escalates."""
        mock_agent.return_value = {
            "route": {
                "stops": ["LA", "Phoenix", "Dallas"],
                "distance_km": 2200.0,
                "transit_hours": 24.0,
            },
            "confidence": 0.92,
            "model_tier": "LLM",
            "model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "latency_ms": 3500,
        }

        event = {
            "shipment_id": "ship-003",
            "origin": "Los Angeles, CA",
            "destination": "Dallas, TX",
            "weight_kg": 1000,
        }
        result = handler(event, None)

        assert result["model_tier"] == "LLM"
        assert result["confidence"] == 0.92

    @patch("lambdas.invoke_planning_agent.handler.publish_event")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_publishes_route_optimized_event(self, mock_agent, mock_publish):
        """Handler publishes RouteOptimized event after successful invocation."""
        mock_agent.return_value = {
            "route": {
                "stops": ["Boston, MA", "Hartford, CT", "Philadelphia, PA"],
                "distance_km": 500.0,
                "transit_hours": 6.0,
            },
            "confidence": 0.78,
            "model_tier": "SLM",
            "model_id": "us.amazon.nova-lite-v1:0",
            "latency_ms": 900,
        }

        event = {
            "shipment_id": "ship-004",
            "origin": "Boston, MA",
            "destination": "Philadelphia, PA",
            "weight_kg": 300,
        }
        handler(event, None)

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args[1]
        assert call_kwargs["event_type"] == "RouteOptimized"
        assert call_kwargs["shipment_id"] == "ship-004"
        assert call_kwargs["step"] == "OptimizeRoute"
        assert call_kwargs["payload"]["route"]["stops"] == ["Boston, MA", "Hartford, CT", "Philadelphia, PA"]
        # AI step: model_tier and confidence are top-level args, not nested in payload.
        assert call_kwargs["confidence"] == 0.78
        assert call_kwargs["model_tier"] == "SLM"
        assert "confidence" not in call_kwargs["payload"]
        assert "model_tier" not in call_kwargs["payload"]
        # Verify timestamp is valid ISO-8601
        datetime.fromisoformat(call_kwargs["timestamp"])

    @patch("lambdas.invoke_planning_agent.handler.publish_event")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_no_status_code_on_success(self, mock_agent, mock_publish):
        """Successful response returns flat dict without statusCode."""
        mock_agent.return_value = {
            "route": {"stops": [], "distance_km": 0, "transit_hours": 0},
            "confidence": 0.80,
            "model_tier": "SLM",
            "model_id": "us.amazon.nova-lite-v1:0",
            "latency_ms": 800,
        }

        event = {
            "shipment_id": "ship-005",
            "origin": "Atlanta, GA",
            "destination": "Miami, FL",
            "weight_kg": 150,
        }
        result = handler(event, None)

        assert "statusCode" not in result
        assert "shipment_id" in result
        assert "route" in result
        assert "confidence" in result
        assert "model_tier" in result


class TestHandlerErrorHandling:
    """Tests for error handling when agent invocation fails."""

    @patch("lambdas.invoke_planning_agent.handler.publish_event")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_agent_exception_returns_500(self, mock_agent, mock_publish):
        """Handler returns 500 when Planning Agent raises an exception."""
        mock_agent.side_effect = RuntimeError("Model invocation timeout")

        event = {
            "shipment_id": "ship-err-001",
            "origin": "Chicago, IL",
            "destination": "NYC",
            "weight_kg": 100,
        }
        result = handler(event, None)

        assert result["statusCode"] == 500
        assert "Planning Agent invocation failed" in result["body"]["error"]
        mock_publish.assert_not_called()


def _agent_result(model_tier="SLM", confidence=0.85):
    return {
        "route": {"stops": ["A", "B", "C"], "distance_km": 500.0, "transit_hours": 6.0},
        "confidence": confidence,
        "model_tier": model_tier,
        "model_id": "us.amazon.nova-lite-v1:0",
        "latency_ms": 900,
    }


PLANNING_EVENT = {
    "shipment_id": "ship-123",
    "origin": "Chicago, IL",
    "destination": "New York, NY",
    "weight_kg": 500,
}


class TestPublishEvent:
    """End-to-end tests for the standardized EventBridge envelope.

    Publishing now runs through shared.events.publish_event. This is an AI
    step, so model_tier and confidence must appear at the TOP LEVEL of the
    detail — not nested inside detail["payload"].
    """

    @patch("shared.events._client")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_publishes_to_eventbridge_with_correct_structure(self, mock_agent, mock_client):
        """Event is published with correct source, detail-type, and detail."""
        mock_agent.return_value = _agent_result(model_tier="SLM", confidence=0.85)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {"FailedEntryCount": 0}
        mock_client.return_value = mock_events_client

        handler(dict(PLANNING_EVENT), None)

        mock_events_client.put_events.assert_called_once()
        call_kwargs = mock_events_client.put_events.call_args[1]
        entry = call_kwargs["Entries"][0]
        assert entry["Source"] == "tms.mvp"
        assert entry["DetailType"] == "RouteOptimized"

        detail = json.loads(entry["Detail"])
        assert detail["event_type"] == "RouteOptimized"
        assert detail["shipment_id"] == "ship-123"
        assert detail["step"] == "OptimizeRoute"
        # AI metadata promoted to top level of the detail.
        assert detail["model_tier"] == "SLM"
        assert detail["confidence"] == 0.85
        assert detail["payload"]["route"]["stops"] == ["A", "B", "C"]
        # No longer nested in payload.
        assert "model_tier" not in detail["payload"]
        assert "confidence" not in detail["payload"]

    @patch("shared.events._client")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_uses_event_bus_from_environment(self, mock_agent, mock_client):
        """Event bus name is read from EVENT_BUS_NAME env var."""
        mock_agent.return_value = _agent_result(model_tier="LLM", confidence=0.80)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {"FailedEntryCount": 0}
        mock_client.return_value = mock_events_client

        with patch.dict(os.environ, {"EVENT_BUS_NAME": "custom-bus"}):
            handler(dict(PLANNING_EVENT), None)

        call_kwargs = mock_events_client.put_events.call_args[1]
        entry = call_kwargs["Entries"][0]
        assert entry["EventBusName"] == "custom-bus"

    @patch("shared.events._client")
    @patch("lambdas.invoke_planning_agent.handler.invoke_planning_agent")
    def test_handles_eventbridge_failure_gracefully(self, mock_agent, mock_client):
        """EventBridge publish failure doesn't raise — logged and swallowed."""
        mock_agent.return_value = _agent_result(model_tier="SLM", confidence=0.70)
        mock_events_client = MagicMock()
        mock_events_client.put_events.side_effect = Exception("Network error")
        mock_client.return_value = mock_events_client

        # Should not raise
        result = handler(dict(PLANNING_EVENT), None)
        assert result["model_tier"] == "SLM"
