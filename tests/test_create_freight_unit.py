"""Unit tests for the create_freight_unit Lambda handler."""

import json
import sys
import os
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lambdas.create_freight_unit.handler import (
    handler,
    _check_idempotency,
    _store_idempotency,
)


# --- Valid input fixtures ---

VALID_INPUT = {
    "origin": "Chicago, IL",
    "destination": "New York, NY",
    "weight_kg": 750,
    "commodity": "Electronics",
    "delivery_date": "2025-02-15",
}


class TestInputValidation:
    """Tests for required field validation."""

    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_missing_origin(self, mock_put):
        event = {**VALID_INPUT}
        del event["origin"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "origin" in result["error"]
        mock_put.assert_not_called()

    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_missing_destination(self, mock_put):
        event = {**VALID_INPUT}
        del event["destination"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "destination" in result["error"]
        mock_put.assert_not_called()

    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_missing_weight_kg(self, mock_put):
        event = {**VALID_INPUT}
        del event["weight_kg"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "weight_kg" in result["error"]
        mock_put.assert_not_called()

    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_missing_commodity(self, mock_put):
        event = {**VALID_INPUT}
        del event["commodity"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "commodity" in result["error"]
        mock_put.assert_not_called()

    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_missing_delivery_date(self, mock_put):
        event = {**VALID_INPUT}
        del event["delivery_date"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "delivery_date" in result["error"]
        mock_put.assert_not_called()

    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_multiple_missing_fields(self, mock_put):
        event = {"origin": "Chicago, IL"}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "destination" in result["error"]
        assert "weight_kg" in result["error"]
        mock_put.assert_not_called()

    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_null_field_treated_as_missing(self, mock_put):
        event = {**VALID_INPUT, "commodity": None}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "commodity" in result["error"]
        mock_put.assert_not_called()


class TestSuccessfulCreation:
    """Tests for the happy-path creation flow."""

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_returns_required_fields(self, mock_put_shipment, mock_put_event, mock_publish):
        result = handler(VALID_INPUT, None)

        assert "freight_unit_id" in result
        assert "shipment_id" in result
        assert "status" in result
        assert "created_at" in result

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_status_is_created(self, mock_put_shipment, mock_put_event, mock_publish):
        result = handler(VALID_INPUT, None)
        assert result["status"] == "created"

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_ids_are_valid_uuids(self, mock_put_shipment, mock_put_event, mock_publish):
        import uuid

        result = handler(VALID_INPUT, None)
        # Should not raise ValueError
        uuid.UUID(result["freight_unit_id"])
        uuid.UUID(result["shipment_id"])

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_created_at_is_iso_timestamp(self, mock_put_shipment, mock_put_event, mock_publish):
        result = handler(VALID_INPUT, None)
        # Should parse without error
        datetime.fromisoformat(result["created_at"])

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_writes_shipment_to_dynamo(self, mock_put_shipment, mock_put_event, mock_publish):
        result = handler(VALID_INPUT, None)

        mock_put_shipment.assert_called_once()
        args = mock_put_shipment.call_args
        shipment_id = args[0][0]
        attributes = args[0][1]

        assert shipment_id == result["shipment_id"]
        assert attributes["origin"] == "Chicago, IL"
        assert attributes["destination"] == "New York, NY"
        assert attributes["weight_kg"] == "750.0"
        assert attributes["commodity"] == "Electronics"
        assert attributes["delivery_date"] == "2025-02-15"
        assert attributes["status"] == "created"

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_stores_creation_event(self, mock_put_shipment, mock_put_event, mock_publish):
        result = handler(VALID_INPUT, None)

        mock_put_event.assert_called_once()
        args = mock_put_event.call_args
        shipment_id = args[0][0]
        event_data = args[0][2]

        assert shipment_id == result["shipment_id"]
        assert event_data["event_type"] == "ShipmentCreated"
        assert event_data["shipment_id"] == result["shipment_id"]
        assert event_data["freight_unit_id"] == result["freight_unit_id"]

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_publishes_eventbridge_event(self, mock_put_shipment, mock_put_event, mock_publish):
        result = handler(VALID_INPUT, None)

        mock_publish.assert_called_once()
        kwargs = mock_publish.call_args[1]
        assert kwargs["event_type"] == "ShipmentCreated"
        assert kwargs["shipment_id"] == result["shipment_id"]
        assert kwargs["step"] == "CreateFreightUnit"
        payload = kwargs["payload"]
        assert payload["freight_unit_id"] == result["freight_unit_id"]
        assert payload["origin"] == "Chicago, IL"
        assert payload["destination"] == "New York, NY"
        assert payload["weight_kg"] == 750.0
        assert payload["commodity"] == "Electronics"
        assert payload["delivery_date"] == "2025-02-15"

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_weight_as_string(self, mock_put_shipment, mock_put_event, mock_publish):
        """Verify weight_kg can be passed as string (from JSON payloads)."""
        event = {**VALID_INPUT, "weight_kg": "500.5"}
        result = handler(event, None)

        assert result["status"] == "created"
        args = mock_put_shipment.call_args
        attributes = args[0][1]
        assert attributes["weight_kg"] == "500.5"


class TestIdempotency:
    """Tests for idempotency handling."""

    @patch("lambdas.create_freight_unit.handler.get_item")
    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    @patch("lambdas.create_freight_unit.handler.put_item")
    def test_idempotency_returns_cached_response(
        self, mock_put_item, mock_put_shipment, mock_put_event, mock_publish, mock_get_item
    ):
        """If an idempotency_key matches an existing record, return cached response."""
        cached_response = {
            "freight_unit_id": "cached-fu-id",
            "shipment_id": "cached-ship-id",
            "status": "created",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        mock_get_item.return_value = {
            "PK": "IDEMPOTENCY#test-key",
            "SK": "CREATE_FREIGHT_UNIT",
            "response": json.dumps(cached_response),
        }

        event = {**VALID_INPUT, "idempotency_key": "test-key"}
        result = handler(event, None)

        assert result == cached_response
        mock_put_shipment.assert_not_called()
        mock_publish.assert_not_called()

    @patch("lambdas.create_freight_unit.handler.get_item")
    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    @patch("lambdas.create_freight_unit.handler.put_item")
    def test_idempotency_stores_new_response(
        self, mock_put_item, mock_put_shipment, mock_put_event, mock_publish, mock_get_item
    ):
        """First call with idempotency_key stores the response for future retries."""
        mock_get_item.return_value = None  # No cached result

        event = {**VALID_INPUT, "idempotency_key": "new-key"}
        result = handler(event, None)

        # Should still create the shipment
        mock_put_shipment.assert_called_once()
        # Should store idempotency record
        mock_put_item.assert_called_once()
        stored_item = mock_put_item.call_args[0][0]
        assert stored_item["PK"] == "IDEMPOTENCY#new-key"
        assert stored_item["SK"] == "CREATE_FREIGHT_UNIT"
        stored_response = json.loads(stored_item["response"])
        assert stored_response == result

    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_no_idempotency_key_skips_check(self, mock_put_shipment, mock_put_event, mock_publish):
        """Without an idempotency_key, no idempotency check or storage happens."""
        result = handler(VALID_INPUT, None)

        assert "shipment_id" in result
        mock_put_shipment.assert_called_once()


class TestPublishEvent:
    """End-to-end tests for the standardized EventBridge envelope.

    Event publishing now goes through shared.events.publish_event, so these
    tests run the handler with the real publisher and assert the exact
    standardized envelope reaches EventBridge.
    """

    @patch("shared.events._client")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_publishes_correct_event_structure(
        self, mock_put_shipment, mock_put_event, mock_client
    ):
        mock_events = MagicMock()
        mock_client.return_value = mock_events
        mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

        result = handler(VALID_INPUT, None)

        mock_events.put_events.assert_called_once()
        entries = mock_events.put_events.call_args[1]["Entries"]
        assert len(entries) == 1

        entry = entries[0]
        assert entry["Source"] == "tms.mvp"
        assert entry["DetailType"] == "ShipmentCreated"

        detail = json.loads(entry["Detail"])
        assert detail["event_type"] == "ShipmentCreated"
        assert detail["shipment_id"] == result["shipment_id"]
        assert detail["step"] == "CreateFreightUnit"
        # Deterministic step — no AI metadata.
        assert detail["model_tier"] is None
        assert detail["confidence"] is None
        assert detail["payload"]["freight_unit_id"] == result["freight_unit_id"]
        assert detail["payload"]["origin"] == "Chicago, IL"
        assert detail["payload"]["destination"] == "New York, NY"
        assert detail["payload"]["weight_kg"] == 750.0

    @patch("shared.events._client")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_event_publish_failure_does_not_raise(
        self, mock_put_shipment, mock_put_event, mock_client
    ):
        """EventBridge publish failures are logged but don't fail the Lambda."""
        mock_events = MagicMock()
        mock_client.return_value = mock_events
        mock_events.put_events.side_effect = Exception("EventBridge unavailable")

        # Should not raise
        result = handler(VALID_INPUT, None)
        assert result["status"] == "created"

    @patch("shared.events._client")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_event_publish_uses_env_bus_name(
        self, mock_put_shipment, mock_put_event, mock_client
    ):
        """Uses EVENT_BUS_NAME env var if set."""
        mock_events = MagicMock()
        mock_client.return_value = mock_events
        mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

        with patch.dict(os.environ, {"EVENT_BUS_NAME": "custom-bus"}):
            handler(VALID_INPUT, None)

        entries = mock_events.put_events.call_args[1]["Entries"]
        assert entries[0]["EventBusName"] == "custom-bus"
