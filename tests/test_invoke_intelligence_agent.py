"""Unit tests for the invoke_intelligence_agent Lambda handler."""

import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.invoke_intelligence_agent.handler import handler


class TestHandlerValidation:
    """Tests for input validation in the handler."""

    def test_missing_shipment_id_returns_400(self):
        """Handler returns 400 when shipment_id is missing."""
        event = {"exception_type": "delay", "severity_hint": "medium"}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "shipment_id" in str(result["body"]["error"])

    def test_missing_exception_type_returns_400(self):
        """Handler returns 400 when exception_type is missing."""
        event = {"shipment_id": "s-001", "severity_hint": "medium"}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "exception_type" in str(result["body"]["error"])

    def test_missing_severity_hint_returns_400(self):
        """Handler returns 400 when severity_hint is missing."""
        event = {"shipment_id": "s-001", "exception_type": "delay"}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "severity_hint" in str(result["body"]["error"])

    def test_multiple_missing_fields_returns_400(self):
        """Handler returns 400 listing all missing fields."""
        result = handler({}, None)
        assert result["statusCode"] == 400
        error_msg = str(result["body"]["error"])
        assert "shipment_id" in error_msg
        assert "exception_type" in error_msg
        assert "severity_hint" in error_msg


class TestHandlerHappyPath:
    """Tests for the successful Intelligence Agent invocation flow."""

    @patch("lambdas.invoke_intelligence_agent.handler.publish_event")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_returns_exception_analysis_from_agent(self, mock_agent, mock_publish):
        """Handler returns the exception analysis result from the Intelligence Agent."""
        mock_agent.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "Reroute shipment via alternate corridor.",
            "confidence": 0.82,
            "model_tier": "SLM",
            "model_id": "us.amazon.nova-lite-v1:0",
            "latency_ms": 1100,
        }

        event = {
            "shipment_id": "ship-001",
            "exception_type": "delay",
            "severity_hint": "medium",
        }
        result = handler(event, None)

        assert result["shipment_id"] == "ship-001"
        assert result["exception_type"] == "delay"
        assert result["severity"] == "medium"
        assert result["resolution_recommendation"] == "Reroute shipment via alternate corridor."
        assert result["confidence"] == 0.82
        assert result["model_tier"] == "SLM"

    @patch("lambdas.invoke_intelligence_agent.handler.publish_event")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_passes_correct_payload_to_agent(self, mock_agent, mock_publish):
        """Handler constructs the correct payload for the Intelligence Agent."""
        mock_agent.return_value = {
            "exception_type": "damage",
            "severity": "high",
            "resolution_recommendation": "Initiate damage claim.",
            "confidence": 0.88,
            "model_tier": "LLM",
            "model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "latency_ms": 2500,
        }

        event = {
            "shipment_id": "ship-002",
            "exception_type": "damage",
            "severity_hint": "high",
        }
        handler(event, None)

        mock_agent.assert_called_once_with({
            "shipment_id": "ship-002",
            "exception_type": "damage",
            "severity_hint": "high",
        })

    @patch("lambdas.invoke_intelligence_agent.handler.publish_event")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_returns_llm_model_tier_when_escalated(self, mock_agent, mock_publish):
        """Handler returns LLM model_tier when agent escalates."""
        mock_agent.return_value = {
            "exception_type": "customs_hold",
            "severity": "medium",
            "resolution_recommendation": "Contact customs broker.",
            "confidence": 0.91,
            "model_tier": "LLM",
            "model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
            "latency_ms": 3000,
        }

        event = {
            "shipment_id": "ship-003",
            "exception_type": "customs_hold",
            "severity_hint": "medium",
        }
        result = handler(event, None)

        assert result["model_tier"] == "LLM"
        assert result["confidence"] == 0.91

    @patch("lambdas.invoke_intelligence_agent.handler.publish_event")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_publishes_exception_resolved_event(self, mock_agent, mock_publish):
        """Handler publishes ExceptionResolved event after successful invocation."""
        mock_agent.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "Adjust ETA and notify customer.",
            "confidence": 0.85,
            "model_tier": "SLM",
            "model_id": "us.amazon.nova-lite-v1:0",
            "latency_ms": 950,
        }

        event = {
            "shipment_id": "ship-004",
            "exception_type": "delay",
            "severity_hint": "medium",
        }
        handler(event, None)

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args[1]
        assert call_kwargs["event_type"] == "ExceptionResolved"
        assert call_kwargs["shipment_id"] == "ship-004"
        assert call_kwargs["step"] == "AnalyzeException"
        assert call_kwargs["payload"]["exception_type"] == "delay"
        assert call_kwargs["payload"]["severity"] == "medium"
        assert call_kwargs["payload"]["resolution_recommendation"] == "Adjust ETA and notify customer."
        # AI step: model_tier and confidence are top-level args, not nested in payload.
        assert call_kwargs["confidence"] == 0.85
        assert call_kwargs["model_tier"] == "SLM"
        assert "confidence" not in call_kwargs["payload"]
        assert "model_tier" not in call_kwargs["payload"]
        # Verify timestamp is valid ISO-8601
        datetime.fromisoformat(call_kwargs["timestamp"])

    @patch("lambdas.invoke_intelligence_agent.handler.publish_event")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_no_status_code_on_success(self, mock_agent, mock_publish):
        """Successful response returns flat dict without statusCode."""
        mock_agent.return_value = {
            "exception_type": "missing",
            "severity": "high",
            "resolution_recommendation": "Trigger carrier investigation.",
            "confidence": 0.77,
            "model_tier": "SLM",
            "model_id": "us.amazon.nova-lite-v1:0",
            "latency_ms": 1050,
        }

        event = {
            "shipment_id": "ship-005",
            "exception_type": "missing",
            "severity_hint": "high",
        }
        result = handler(event, None)

        assert "statusCode" not in result
        assert "shipment_id" in result
        assert "exception_type" in result
        assert "severity" in result
        assert "resolution_recommendation" in result
        assert "confidence" in result
        assert "model_tier" in result


class TestHandlerErrorHandling:
    """Tests for error handling when agent invocation fails."""

    @patch("lambdas.invoke_intelligence_agent.handler.publish_event")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_agent_exception_returns_500(self, mock_agent, mock_publish):
        """Handler returns 500 when Intelligence Agent raises an exception."""
        mock_agent.side_effect = RuntimeError("Model invocation timeout")

        event = {
            "shipment_id": "ship-err-001",
            "exception_type": "delay",
            "severity_hint": "medium",
        }
        result = handler(event, None)

        assert result["statusCode"] == 500
        assert "Intelligence Agent invocation failed" in result["body"]["error"]
        mock_publish.assert_not_called()


def _agent_result(model_tier="SLM", confidence=0.85):
    return {
        "exception_type": "delay",
        "severity": "medium",
        "resolution_recommendation": "Reroute via alternate corridor.",
        "confidence": confidence,
        "model_tier": model_tier,
        "model_id": "us.amazon.nova-lite-v1:0",
        "latency_ms": 1100,
    }


INTELLIGENCE_EVENT = {
    "shipment_id": "ship-123",
    "exception_type": "delay",
    "severity_hint": "medium",
}


class TestPublishEvent:
    """End-to-end tests for the standardized EventBridge envelope.

    Publishing now runs through shared.events.publish_event. This is an AI
    step, so model_tier and confidence must appear at the TOP LEVEL of the
    detail — not nested inside detail["payload"].
    """

    @patch("shared.events._client")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_publishes_to_eventbridge_with_correct_structure(self, mock_agent, mock_client):
        """Event is published with correct source, detail-type, and detail."""
        mock_agent.return_value = _agent_result(model_tier="SLM", confidence=0.85)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {"FailedEntryCount": 0}
        mock_client.return_value = mock_events_client

        handler(dict(INTELLIGENCE_EVENT), None)

        mock_events_client.put_events.assert_called_once()
        call_kwargs = mock_events_client.put_events.call_args[1]
        entry = call_kwargs["Entries"][0]
        assert entry["Source"] == "tms.mvp"
        assert entry["DetailType"] == "ExceptionResolved"

        detail = json.loads(entry["Detail"])
        assert detail["event_type"] == "ExceptionResolved"
        assert detail["shipment_id"] == "ship-123"
        assert detail["step"] == "AnalyzeException"
        # AI metadata promoted to top level of the detail.
        assert detail["model_tier"] == "SLM"
        assert detail["confidence"] == 0.85
        assert detail["payload"]["exception_type"] == "delay"
        assert detail["payload"]["severity"] == "medium"
        assert detail["payload"]["resolution_recommendation"] == "Reroute via alternate corridor."
        # No longer nested in payload.
        assert "model_tier" not in detail["payload"]
        assert "confidence" not in detail["payload"]

    @patch("shared.events._client")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_uses_event_bus_from_environment(self, mock_agent, mock_client):
        """Event bus name is read from EVENT_BUS_NAME env var."""
        mock_agent.return_value = _agent_result(model_tier="LLM", confidence=0.88)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {"FailedEntryCount": 0}
        mock_client.return_value = mock_events_client

        with patch.dict(os.environ, {"EVENT_BUS_NAME": "custom-bus"}):
            handler(dict(INTELLIGENCE_EVENT), None)

        call_kwargs = mock_events_client.put_events.call_args[1]
        entry = call_kwargs["Entries"][0]
        assert entry["EventBusName"] == "custom-bus"

    @patch("shared.events._client")
    @patch("lambdas.invoke_intelligence_agent.handler.invoke_intelligence_agent")
    def test_handles_eventbridge_failure_gracefully(self, mock_agent, mock_client):
        """EventBridge publish failure doesn't raise — logged and swallowed."""
        mock_agent.return_value = _agent_result(model_tier="SLM", confidence=0.77)
        mock_events_client = MagicMock()
        mock_events_client.put_events.side_effect = Exception("Network error")
        mock_client.return_value = mock_events_client

        # Should not raise
        result = handler(dict(INTELLIGENCE_EVENT), None)
        assert result["model_tier"] == "SLM"
