"""Unit tests for the tms-complete-shipment Lambda handler."""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.complete_shipment.handler import handler


class TestHandler:
    """Tests for the complete_shipment handler function."""

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

    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_shipment_not_found_returns_404(self, mock_get):
        """Handler returns 404 when shipment doesn't exist in DynamoDB."""
        mock_get.return_value = None
        result = handler({"shipment_id": "nonexistent-id"}, None)
        assert result["statusCode"] == 404
        assert "not found" in result["body"]["error"].lower()

    @patch("lambdas.complete_shipment.handler.publish_event")
    @patch("lambdas.complete_shipment.handler.put_shipment_event")
    @patch("lambdas.complete_shipment.handler.update_shipment")
    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_successful_completion(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Handler transitions shipment to complete and returns expected fields."""
        mock_get.return_value = {
            "shipment_id": "test-ship-001",
            "status": "in_transit",
            "origin": "Chicago, IL",
            "destination": "New York, NY",
        }
        mock_update.return_value = {}
        mock_put_event.return_value = {}

        result = handler({"shipment_id": "test-ship-001"}, None)

        assert result["shipment_id"] == "test-ship-001"
        assert result["final_status"] == "complete"
        assert "completed_at" in result
        # completed_at should be a valid ISO-8601 timestamp
        datetime.fromisoformat(result["completed_at"])

    @patch("lambdas.complete_shipment.handler.publish_event")
    @patch("lambdas.complete_shipment.handler.put_shipment_event")
    @patch("lambdas.complete_shipment.handler.update_shipment")
    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_dynamo_updated_with_complete_status(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Handler updates DynamoDB with complete status and timestamps."""
        mock_get.return_value = {
            "shipment_id": "test-ship-002",
            "status": "in_transit",
            "origin": "Seattle, WA",
            "destination": "Denver, CO",
        }
        mock_update.return_value = {}
        mock_put_event.return_value = {}

        handler({"shipment_id": "test-ship-002"}, None)

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == "test-ship-002"
        updates = call_args[0][1]
        assert updates["status"] == "complete"
        assert "completed_at" in updates
        assert "updated_at" in updates
        # Verify timestamps are valid ISO-8601
        datetime.fromisoformat(updates["completed_at"])
        datetime.fromisoformat(updates["updated_at"])

    @patch("lambdas.complete_shipment.handler.publish_event")
    @patch("lambdas.complete_shipment.handler.put_shipment_event")
    @patch("lambdas.complete_shipment.handler.update_shipment")
    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_shipment_event_stored_in_dynamo(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Handler stores the ShipmentCompleted event in DynamoDB."""
        mock_get.return_value = {
            "shipment_id": "test-ship-003",
            "status": "in_transit",
            "origin": "Atlanta, GA",
            "destination": "Miami, FL",
        }
        mock_update.return_value = {}
        mock_put_event.return_value = {}

        handler({"shipment_id": "test-ship-003"}, None)

        mock_put_event.assert_called_once()
        call_args = mock_put_event.call_args
        assert call_args[0][0] == "test-ship-003"
        # Second arg is the timestamp
        datetime.fromisoformat(call_args[0][1])
        # Third arg is the event data
        event_data = call_args[0][2]
        assert event_data["event_type"] == "ShipmentCompleted"
        assert event_data["shipment_id"] == "test-ship-003"
        payload = json.loads(event_data["payload"])
        assert payload["final_status"] == "complete"

    @patch("lambdas.complete_shipment.handler.publish_event")
    @patch("lambdas.complete_shipment.handler.put_shipment_event")
    @patch("lambdas.complete_shipment.handler.update_shipment")
    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_eventbridge_event_published(
        self, mock_get, mock_update, mock_put_event, mock_publish
    ):
        """Handler publishes ShipmentCompleted event to EventBridge."""
        mock_get.return_value = {
            "shipment_id": "test-ship-004",
            "status": "in_transit",
            "origin": "Boston, MA",
            "destination": "Philadelphia, PA",
        }
        mock_update.return_value = {}
        mock_put_event.return_value = {}

        handler({"shipment_id": "test-ship-004"}, None)

        mock_publish.assert_called_once()
        call_kwargs = mock_publish.call_args[1]
        assert call_kwargs["event_type"] == "ShipmentCompleted"
        assert call_kwargs["shipment_id"] == "test-ship-004"
        assert call_kwargs["step"] == "CompleteShipment"
        assert "timestamp" in call_kwargs
        datetime.fromisoformat(call_kwargs["timestamp"])
        assert call_kwargs["payload"]["final_status"] == "complete"


SHIPMENT_FIXTURE = {
    "shipment_id": "ship-001",
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
    @patch("lambdas.complete_shipment.handler.put_shipment_event")
    @patch("lambdas.complete_shipment.handler.update_shipment")
    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_publish_event_calls_eventbridge(
        self, mock_get, mock_update, mock_put_event, mock_client
    ):
        """EventBridge put_events is called with the standardized envelope."""
        mock_get.return_value = dict(SHIPMENT_FIXTURE)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}
        mock_client.return_value = mock_events_client

        handler({"shipment_id": "ship-001"}, None)

        mock_events_client.put_events.assert_called_once()
        call_args = mock_events_client.put_events.call_args[1]
        entries = call_args["Entries"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["Source"] == "tms.mvp"
        assert entry["DetailType"] == "ShipmentCompleted"
        detail = json.loads(entry["Detail"])
        assert detail["event_type"] == "ShipmentCompleted"
        assert detail["shipment_id"] == "ship-001"
        assert detail["step"] == "CompleteShipment"
        # Deterministic step — no AI metadata.
        assert detail["model_tier"] is None
        assert detail["confidence"] is None
        assert detail["payload"]["final_status"] == "complete"

    @patch("shared.events._client")
    @patch("lambdas.complete_shipment.handler.put_shipment_event")
    @patch("lambdas.complete_shipment.handler.update_shipment")
    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_publish_event_uses_env_bus_name(
        self, mock_get, mock_update, mock_put_event, mock_client
    ):
        """EventBridge bus name comes from environment variable when set."""
        mock_get.return_value = dict(SHIPMENT_FIXTURE)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}
        mock_client.return_value = mock_events_client

        with patch.dict(os.environ, {"EVENT_BUS_NAME": "custom-bus"}):
            handler({"shipment_id": "ship-002"}, None)

        call_args = mock_events_client.put_events.call_args[1]
        entry = call_args["Entries"][0]
        assert entry["EventBusName"] == "custom-bus"

    @patch("shared.events._client")
    @patch("lambdas.complete_shipment.handler.put_shipment_event")
    @patch("lambdas.complete_shipment.handler.update_shipment")
    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_publish_event_handles_failure_gracefully(
        self, mock_get, mock_update, mock_put_event, mock_client
    ):
        """EventBridge publish failure does not raise an exception."""
        mock_get.return_value = dict(SHIPMENT_FIXTURE)
        mock_events_client = MagicMock()
        mock_events_client.put_events.side_effect = Exception("Network error")
        mock_client.return_value = mock_events_client

        # Should not raise
        result = handler({"shipment_id": "ship-003"}, None)
        assert result["final_status"] == "complete"

    @patch("shared.events._client")
    @patch("lambdas.complete_shipment.handler.put_shipment_event")
    @patch("lambdas.complete_shipment.handler.update_shipment")
    @patch("lambdas.complete_shipment.handler.get_shipment")
    def test_publish_event_logs_warning_on_partial_failure(
        self, mock_get, mock_update, mock_put_event, mock_client
    ):
        """Partial EventBridge failures are handled without raising."""
        mock_get.return_value = dict(SHIPMENT_FIXTURE)
        mock_events_client = MagicMock()
        mock_events_client.put_events.return_value = {
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "InternalError", "ErrorMessage": "test"}],
        }
        mock_client.return_value = mock_events_client

        # Should not raise
        result = handler({"shipment_id": "ship-004"}, None)
        assert result["final_status"] == "complete"
