"""Unit tests for the tms-detect-exception Lambda handler."""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.detect_exception.handler import handler


class TestHandlerValidation:
    """Tests for input validation in the handler."""

    def test_missing_shipment_id_returns_400(self):
        """Handler returns 400 when shipment_id is missing."""
        result = handler({}, None)
        assert result["statusCode"] == 400
        assert "error" in result["body"]
        assert "shipment_id" in result["body"]["error"]

    def test_empty_shipment_id_returns_400(self):
        """Handler returns 400 when shipment_id is empty string."""
        result = handler({"shipment_id": ""}, None)
        assert result["statusCode"] == 400
        assert "error" in result["body"]

    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_shipment_not_found_returns_404(self, mock_get):
        """Handler returns 404 when shipment doesn't exist in DynamoDB."""
        mock_get.return_value = None
        result = handler({"shipment_id": "nonexistent-id"}, None)
        assert result["statusCode"] == 404
        assert "not found" in result["body"]["error"].lower()


class TestHandlerHappyPath:
    """Tests for the successful exception detection flow."""

    @patch("lambdas.detect_exception.handler.publish_event")
    @patch("lambdas.detect_exception.handler.put_shipment_event")
    @patch("lambdas.detect_exception.handler.update_shipment")
    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_always_detects_delay_exception(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Handler always detects a 'delay' exception for demo purposes."""
        mock_get.return_value = {
            "shipment_id": "test-ship-001",
            "status": "in_transit",
            "origin": "Chicago, IL",
            "destination": "New York, NY",
        }
        mock_update.return_value = {}

        result = handler({"shipment_id": "test-ship-001"}, None)

        assert result["exception_detected"] is True
        assert result["exception_type"] == "delay"
        assert result["severity_hint"] == "medium"
        assert result["shipment_id"] == "test-ship-001"

    @patch("lambdas.detect_exception.handler.publish_event")
    @patch("lambdas.detect_exception.handler.put_shipment_event")
    @patch("lambdas.detect_exception.handler.update_shipment")
    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_updates_dynamo_with_exception_status(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Handler updates DynamoDB shipment status to 'exception'."""
        mock_get.return_value = {
            "shipment_id": "test-ship-002",
            "status": "in_transit",
            "origin": "Seattle, WA",
            "destination": "Denver, CO",
        }
        mock_update.return_value = {}

        handler({"shipment_id": "test-ship-002"}, None)

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == "test-ship-002"
        updates = call_args[0][1]
        assert updates["status"] == "exception"
        assert updates["exception_type"] == "delay"
        assert updates["severity_hint"] == "medium"
        assert "exception_detected_at" in updates
        assert "updated_at" in updates
        # Verify timestamps are valid ISO-8601
        datetime.fromisoformat(updates["exception_detected_at"])
        datetime.fromisoformat(updates["updated_at"])

    @patch("lambdas.detect_exception.handler.publish_event")
    @patch("lambdas.detect_exception.handler.put_shipment_event")
    @patch("lambdas.detect_exception.handler.update_shipment")
    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_stores_exception_event_in_dynamo(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Handler stores an ExceptionDetected event record in DynamoDB."""
        mock_get.return_value = {
            "shipment_id": "test-ship-003",
            "status": "in_transit",
            "origin": "Atlanta, GA",
            "destination": "Miami, FL",
        }
        mock_update.return_value = {}

        handler({"shipment_id": "test-ship-003"}, None)

        mock_put_event.assert_called_once()
        call_args = mock_put_event.call_args
        assert call_args[0][0] == "test-ship-003"
        # Second arg is the timestamp
        timestamp = call_args[0][1]
        datetime.fromisoformat(timestamp)
        # Third arg is the event data
        event_data = call_args[0][2]
        assert event_data["event_type"] == "ExceptionDetected"
        assert event_data["shipment_id"] == "test-ship-003"
        payload = json.loads(event_data["payload"])
        assert payload["exception_type"] == "delay"
        assert payload["severity_hint"] == "medium"

    @patch("lambdas.detect_exception.handler.publish_event")
    @patch("lambdas.detect_exception.handler.put_shipment_event")
    @patch("lambdas.detect_exception.handler.update_shipment")
    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_publishes_eventbridge_event(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Handler publishes ExceptionDetected event to EventBridge."""
        mock_get.return_value = {
            "shipment_id": "test-ship-004",
            "status": "in_transit",
            "origin": "Dallas, TX",
            "destination": "Los Angeles, CA",
        }
        mock_update.return_value = {}

        handler({"shipment_id": "test-ship-004"}, None)

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args[1]
        assert call_kwargs["event_type"] == "ExceptionDetected"
        assert call_kwargs["shipment_id"] == "test-ship-004"
        assert call_kwargs["step"] == "DetectException"
        assert call_kwargs["payload"]["exception_type"] == "delay"
        assert call_kwargs["payload"]["severity_hint"] == "medium"
        assert "timestamp" in call_kwargs
        datetime.fromisoformat(call_kwargs["timestamp"])

    @patch("lambdas.detect_exception.handler.publish_event")
    @patch("lambdas.detect_exception.handler.put_shipment_event")
    @patch("lambdas.detect_exception.handler.update_shipment")
    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_response_has_no_status_code_on_success(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Successful response returns flat dict without statusCode (Step Functions pattern)."""
        mock_get.return_value = {
            "shipment_id": "test-ship-005",
            "status": "in_transit",
            "origin": "Boston, MA",
            "destination": "Philadelphia, PA",
        }
        mock_update.return_value = {}

        result = handler({"shipment_id": "test-ship-005"}, None)

        # Successful responses in Step Functions don't use statusCode wrapper
        assert "statusCode" not in result
        assert "shipment_id" in result
        assert "exception_detected" in result
        assert "exception_type" in result
        assert "severity_hint" in result


SHIPMENT_FIXTURE = {
    "shipment_id": "ship-123",
    "status": "in_transit",
    "origin": "Chicago, IL",
    "destination": "New York, NY",
}


class TestPublishEvent:
    """End-to-end tests for the standardized EventBridge envelope.

    Publishing now runs through shared.events.publish_event, so these tests
    drive the handler with the real publisher and assert the envelope on the
    wire.
    """

    @patch("shared.events._client")
    @patch("lambdas.detect_exception.handler.put_shipment_event")
    @patch("lambdas.detect_exception.handler.update_shipment")
    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_publishes_to_eventbridge_with_correct_structure(
        self, mock_get, mock_update, mock_put_event, mock_client
    ):
        """Event is published with correct source, detail-type, and detail."""
        mock_get.return_value = dict(SHIPMENT_FIXTURE)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {"FailedEntryCount": 0}
        mock_client.return_value = mock_events_client

        handler({"shipment_id": "ship-123"}, None)

        mock_events_client.put_events.assert_called_once()
        call_kwargs = mock_events_client.put_events.call_args[1]
        entry = call_kwargs["Entries"][0]
        assert entry["Source"] == "tms.mvp"
        assert entry["DetailType"] == "ExceptionDetected"

        detail = json.loads(entry["Detail"])
        assert detail["event_type"] == "ExceptionDetected"
        assert detail["shipment_id"] == "ship-123"
        assert detail["step"] == "DetectException"
        # Deterministic step — no AI metadata.
        assert detail["model_tier"] is None
        assert detail["confidence"] is None
        assert detail["payload"]["exception_type"] == "delay"
        assert detail["payload"]["severity_hint"] == "medium"

    @patch("shared.events._client")
    @patch("lambdas.detect_exception.handler.put_shipment_event")
    @patch("lambdas.detect_exception.handler.update_shipment")
    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_uses_event_bus_from_environment(
        self, mock_get, mock_update, mock_put_event, mock_client
    ):
        """Event bus name is read from EVENT_BUS_NAME env var."""
        mock_get.return_value = dict(SHIPMENT_FIXTURE)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {"FailedEntryCount": 0}
        mock_client.return_value = mock_events_client

        with patch.dict(os.environ, {"EVENT_BUS_NAME": "custom-bus"}):
            handler({"shipment_id": "ship-456"}, None)

        call_kwargs = mock_events_client.put_events.call_args[1]
        entry = call_kwargs["Entries"][0]
        assert entry["EventBusName"] == "custom-bus"

    @patch("shared.events._client")
    @patch("lambdas.detect_exception.handler.put_shipment_event")
    @patch("lambdas.detect_exception.handler.update_shipment")
    @patch("lambdas.detect_exception.handler.get_shipment")
    def test_handles_eventbridge_failure_gracefully(
        self, mock_get, mock_update, mock_put_event, mock_client
    ):
        """EventBridge publish failure doesn't raise — logged and swallowed."""
        mock_get.return_value = dict(SHIPMENT_FIXTURE)
        mock_events_client = MagicMock()
        mock_events_client.put_events.side_effect = Exception("Network error")
        mock_client.return_value = mock_events_client

        # Should not raise
        result = handler({"shipment_id": "ship-789"}, None)
        assert result["exception_detected"] is True
