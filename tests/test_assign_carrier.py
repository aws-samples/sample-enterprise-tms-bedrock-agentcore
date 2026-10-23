"""Unit tests for the assign carrier Lambda handler."""

import json
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lambdas.assign_carrier.handler import (
    handler,
    _select_lowest_rate,
    _invoke_carrier_simulator,
    _check_idempotency,
    _store_idempotency,
)


class TestSelectLowestRate:
    """Tests for _select_lowest_rate."""

    def test_selects_lowest_from_multiple_quotes(self):
        quotes = [
            {"carrier_id": "c1", "rate_usd": 1500.0, "carrier_name": "A", "service_type": "ground"},
            {"carrier_id": "c2", "rate_usd": 900.0, "carrier_name": "B", "service_type": "express"},
            {"carrier_id": "c3", "rate_usd": 1200.0, "carrier_name": "C", "service_type": "ground"},
        ]
        result = _select_lowest_rate(quotes)
        assert result["carrier_id"] == "c2"
        assert result["rate_usd"] == 900.0

    def test_selects_single_quote(self):
        quotes = [
            {"carrier_id": "c1", "rate_usd": 1000.0, "carrier_name": "A", "service_type": "ground"},
        ]
        result = _select_lowest_rate(quotes)
        assert result["carrier_id"] == "c1"

    def test_selects_first_when_tied(self):
        quotes = [
            {"carrier_id": "c1", "rate_usd": 800.0, "carrier_name": "A", "service_type": "ground"},
            {"carrier_id": "c2", "rate_usd": 800.0, "carrier_name": "B", "service_type": "express"},
        ]
        result = _select_lowest_rate(quotes)
        # min() returns the first element on a tie
        assert result["carrier_id"] == "c1"


class TestHandlerValidation:
    """Tests for input validation in the handler."""

    def test_missing_shipment_id(self):
        event = {"origin": "Chicago, IL", "destination": "NYC", "weight_kg": 100}
        result = handler(event, None)
        assert "error" in result
        assert result["statusCode"] == 400

    def test_missing_origin(self):
        event = {"shipment_id": "abc-123", "destination": "NYC", "weight_kg": 100}
        result = handler(event, None)
        assert "error" in result
        assert result["statusCode"] == 400

    def test_missing_destination(self):
        event = {"shipment_id": "abc-123", "origin": "Chicago, IL", "weight_kg": 100}
        result = handler(event, None)
        assert "error" in result
        assert result["statusCode"] == 400

    def test_missing_weight(self):
        event = {"shipment_id": "abc-123", "origin": "Chicago, IL", "destination": "NYC"}
        result = handler(event, None)
        assert "error" in result
        assert result["statusCode"] == 400

    def test_invalid_weight_string(self):
        event = {
            "shipment_id": "abc-123",
            "origin": "Chicago, IL",
            "destination": "NYC",
            "weight_kg": "not-a-number",
        }
        result = handler(event, None)
        assert "error" in result
        assert result["statusCode"] == 400

    def test_negative_weight(self):
        event = {
            "shipment_id": "abc-123",
            "origin": "Chicago, IL",
            "destination": "NYC",
            "weight_kg": -10,
        }
        result = handler(event, None)
        assert "error" in result
        assert result["statusCode"] == 400

    def test_zero_weight(self):
        event = {
            "shipment_id": "abc-123",
            "origin": "Chicago, IL",
            "destination": "NYC",
            "weight_kg": 0,
        }
        result = handler(event, None)
        assert "error" in result
        assert result["statusCode"] == 400


class TestInvokeCarrierSimulator:
    """Tests for _invoke_carrier_simulator."""

    @patch("lambdas.assign_carrier.handler.boto3.client")
    def test_successful_invocation(self, mock_boto_client):
        mock_lambda = MagicMock()
        mock_boto_client.return_value = mock_lambda

        simulator_response = {
            "quotes": [
                {
                    "carrier_id": "carrier-a",
                    "carrier_name": "Carrier A",
                    "rate_usd": 1245.0,
                    "transit_days": 3,
                    "service_type": "ground_freight",
                }
            ]
        }
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}

        quotes = _invoke_carrier_simulator("Chicago, IL", "New York, NY", 750.0)

        assert len(quotes) == 1
        assert quotes[0]["carrier_id"] == "carrier-a"
        assert quotes[0]["rate_usd"] == 1245.0

    @patch("lambdas.assign_carrier.handler.boto3.client")
    def test_raises_on_simulator_error(self, mock_boto_client):
        mock_lambda = MagicMock()
        mock_boto_client.return_value = mock_lambda

        simulator_response = {"error": "origin and destination are required", "quotes": []}
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}

        with pytest.raises(RuntimeError, match="Carrier simulator error"):
            _invoke_carrier_simulator("", "New York, NY", 750.0)

    @patch("lambdas.assign_carrier.handler.boto3.client")
    def test_raises_on_empty_quotes(self, mock_boto_client):
        mock_lambda = MagicMock()
        mock_boto_client.return_value = mock_lambda

        simulator_response = {"quotes": []}
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}

        with pytest.raises(RuntimeError, match="no quotes"):
            _invoke_carrier_simulator("Chicago, IL", "New York, NY", 750.0)


class TestHandlerEndToEnd:
    """End-to-end handler tests with mocked dependencies."""

    @patch("shared.events._client")
    @patch("lambdas.assign_carrier.handler.boto3.client")
    @patch("lambdas.assign_carrier.handler.put_shipment_event")
    @patch("lambdas.assign_carrier.handler.update_shipment")
    def test_assigns_lowest_rate_carrier(
        self, mock_update_shipment, mock_put_event, mock_boto_client, mock_events_client
    ):
        # Mock the Lambda client for carrier simulator invocation
        mock_lambda = MagicMock()
        mock_events = MagicMock()

        mock_boto_client.return_value = mock_lambda
        mock_events_client.return_value = mock_events

        simulator_response = {
            "quotes": [
                {
                    "carrier_id": "carrier-b",
                    "carrier_name": "Carrier B",
                    "rate_usd": 1500.0,
                    "transit_days": 2,
                    "service_type": "express_freight",
                },
                {
                    "carrier_id": "carrier-a",
                    "carrier_name": "Carrier A",
                    "rate_usd": 1200.0,
                    "transit_days": 3,
                    "service_type": "ground_freight",
                },
                {
                    "carrier_id": "carrier-c",
                    "carrier_name": "Carrier C",
                    "rate_usd": 1350.0,
                    "transit_days": 4,
                    "service_type": "ltl_freight",
                },
            ]
        }
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}
        mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

        event = {
            "shipment_id": "ship-001",
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": 750,
        }

        result = handler(event, None)

        # Should select Carrier A (lowest rate at $1200)
        assert result["carrier_id"] == "carrier-a"
        assert result["carrier_name"] == "Carrier A"
        assert result["rate_usd"] == 1200.0
        assert result["service_type"] == "ground_freight"

        # Verify DynamoDB shipment was updated
        mock_update_shipment.assert_called_once()
        call_args = mock_update_shipment.call_args
        assert call_args[0][0] == "ship-001"
        updates = call_args[0][1]
        assert updates["status"] == "carrier_assigned"
        assert updates["carrier_id"] == "carrier-a"
        assert updates["carrier_name"] == "Carrier A"
        assert updates["rate_usd"] == "1200.0"

        # Verify event was stored in DynamoDB
        mock_put_event.assert_called_once()

    @patch("shared.events._client")
    @patch("lambdas.assign_carrier.handler.boto3.client")
    @patch("lambdas.assign_carrier.handler.put_shipment_event")
    @patch("lambdas.assign_carrier.handler.update_shipment")
    def test_publishes_carrier_assigned_event(
        self, mock_update_shipment, mock_put_event, mock_boto_client, mock_events_client
    ):
        mock_lambda = MagicMock()
        mock_events = MagicMock()

        # Carrier simulator invoke goes through the handler's boto3.client("lambda").
        mock_boto_client.return_value = mock_lambda
        # Event publishing now goes through shared.events._client().
        mock_events_client.return_value = mock_events

        simulator_response = {
            "quotes": [
                {
                    "carrier_id": "carrier-a",
                    "carrier_name": "Carrier A",
                    "rate_usd": 1000.0,
                    "transit_days": 3,
                    "service_type": "ground_freight",
                }
            ]
        }
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}
        mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

        event = {
            "shipment_id": "ship-002",
            "origin": "Los Angeles, CA",
            "destination": "Dallas, TX",
            "weight_kg": 500,
        }

        handler(event, None)

        # Verify EventBridge event was published
        mock_events.put_events.assert_called_once()
        put_events_call = mock_events.put_events.call_args
        entries = put_events_call[1]["Entries"]
        assert len(entries) == 1
        assert entries[0]["Source"] == "tms.mvp"
        assert entries[0]["DetailType"] == "CarrierAssigned"

        detail = json.loads(entries[0]["Detail"])
        assert detail["event_type"] == "CarrierAssigned"
        assert detail["shipment_id"] == "ship-002"
        assert detail["step"] == "AssignCarrier"
        # Deterministic step — no AI metadata.
        assert detail["model_tier"] is None
        assert detail["confidence"] is None
        assert detail["payload"]["carrier_id"] == "carrier-a"
        assert detail["payload"]["carrier_name"] == "Carrier A"
        assert detail["payload"]["rate_usd"] == 1000.0
        assert detail["payload"]["quotes_evaluated"] == 1

    @patch("lambdas.assign_carrier.handler.boto3.client")
    @patch("lambdas.assign_carrier.handler.put_shipment_event")
    @patch("lambdas.assign_carrier.handler.update_shipment")
    def test_handles_simulator_failure(
        self, mock_update_shipment, mock_put_event, mock_boto_client
    ):
        mock_lambda = MagicMock()

        def client_factory(service_name, **kwargs):
            if service_name == "lambda":
                return mock_lambda
            return MagicMock()

        mock_boto_client.side_effect = client_factory

        simulator_response = {"error": "weight_kg must be positive", "quotes": []}
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}

        event = {
            "shipment_id": "ship-003",
            "origin": "Chicago, IL",
            "destination": "NYC",
            "weight_kg": 500,
        }

        result = handler(event, None)

        assert "error" in result
        assert result["statusCode"] == 502
        # Should NOT update DynamoDB on failure
        mock_update_shipment.assert_not_called()
        mock_put_event.assert_not_called()

    @patch("shared.events._client")
    @patch("lambdas.assign_carrier.handler.boto3.client")
    @patch("lambdas.assign_carrier.handler.put_shipment_event")
    @patch("lambdas.assign_carrier.handler.update_shipment")
    def test_weight_as_string(
        self, mock_update_shipment, mock_put_event, mock_boto_client, mock_events_client
    ):
        """Verify weight_kg can be passed as a string (from JSON)."""
        mock_lambda = MagicMock()
        mock_events = MagicMock()

        mock_boto_client.return_value = mock_lambda
        mock_events_client.return_value = mock_events

        simulator_response = {
            "quotes": [
                {
                    "carrier_id": "carrier-a",
                    "carrier_name": "Carrier A",
                    "rate_usd": 800.0,
                    "transit_days": 3,
                    "service_type": "ground_freight",
                }
            ]
        }
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}
        mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

        event = {
            "shipment_id": "ship-004",
            "origin": "Atlanta, GA",
            "destination": "Miami, FL",
            "weight_kg": "250",  # String value
        }

        result = handler(event, None)

        assert result["carrier_id"] == "carrier-a"
        assert result["rate_usd"] == 800.0


class TestIdempotency:
    """Tests for idempotency handling."""

    @patch("lambdas.assign_carrier.handler.get_item")
    def test_returns_cached_response_on_idempotency_hit(self, mock_get_item):
        cached_response = {
            "carrier_id": "carrier-a",
            "carrier_name": "Carrier A",
            "rate_usd": 1200.0,
            "service_type": "ground_freight",
        }
        mock_get_item.return_value = {
            "PK": "IDEMPOTENCY#key-123",
            "SK": "ASSIGN_CARRIER",
            "response": json.dumps(cached_response),
        }

        event = {
            "shipment_id": "ship-001",
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": 750,
            "idempotency_key": "key-123",
        }

        result = handler(event, None)

        assert result == cached_response
        mock_get_item.assert_called_once_with("IDEMPOTENCY#key-123", "ASSIGN_CARRIER")

    @patch("lambdas.assign_carrier.handler.get_item")
    def test_no_idempotency_check_without_key(self, mock_get_item):
        """If no idempotency_key, skip idempotency check."""
        event = {
            "shipment_id": "ship-001",
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            # No idempotency_key, but also missing weight_kg to trigger validation error
        }

        result = handler(event, None)

        # Should not check idempotency
        mock_get_item.assert_not_called()
        # Should fail validation
        assert "error" in result

    @patch("shared.events._client")
    @patch("lambdas.assign_carrier.handler.put_item")
    @patch("lambdas.assign_carrier.handler.put_shipment_event")
    @patch("lambdas.assign_carrier.handler.update_shipment")
    @patch("lambdas.assign_carrier.handler.get_item")
    @patch("lambdas.assign_carrier.handler.boto3.client")
    def test_stores_idempotency_on_success(
        self,
        mock_boto_client,
        mock_get_item,
        mock_update_shipment,
        mock_put_event,
        mock_put_item,
        mock_events_client,
    ):
        # No existing idempotency record
        mock_get_item.return_value = None

        mock_lambda = MagicMock()
        mock_events = MagicMock()

        mock_boto_client.return_value = mock_lambda
        mock_events_client.return_value = mock_events

        simulator_response = {
            "quotes": [
                {
                    "carrier_id": "carrier-a",
                    "carrier_name": "Carrier A",
                    "rate_usd": 1000.0,
                    "transit_days": 3,
                    "service_type": "ground_freight",
                }
            ]
        }
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}
        mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

        event = {
            "shipment_id": "ship-005",
            "origin": "Seattle, WA",
            "destination": "Denver, CO",
            "weight_kg": 300,
            "idempotency_key": "key-456",
        }

        result = handler(event, None)

        assert result["carrier_id"] == "carrier-a"

        # Verify idempotency record was stored
        mock_put_item.assert_called_once()
        stored_item = mock_put_item.call_args[0][0]
        assert stored_item["PK"] == "IDEMPOTENCY#key-456"
        assert stored_item["SK"] == "ASSIGN_CARRIER"
        stored_response = json.loads(stored_item["response"])
        assert stored_response["carrier_id"] == "carrier-a"


class TestEventBridgeNonBlocking:
    """Tests verifying EventBridge failures don't fail the Lambda."""

    @patch("shared.events._client")
    @patch("lambdas.assign_carrier.handler.boto3.client")
    @patch("lambdas.assign_carrier.handler.put_shipment_event")
    @patch("lambdas.assign_carrier.handler.update_shipment")
    def test_succeeds_even_if_eventbridge_fails(
        self, mock_update_shipment, mock_put_event, mock_boto_client, mock_events_client
    ):
        mock_lambda = MagicMock()
        mock_events = MagicMock()

        mock_boto_client.return_value = mock_lambda
        mock_events_client.return_value = mock_events

        simulator_response = {
            "quotes": [
                {
                    "carrier_id": "carrier-a",
                    "carrier_name": "Carrier A",
                    "rate_usd": 1000.0,
                    "transit_days": 3,
                    "service_type": "ground_freight",
                }
            ]
        }
        mock_payload = MagicMock()
        mock_payload.read.return_value = json.dumps(simulator_response).encode()
        mock_lambda.invoke.return_value = {"Payload": mock_payload}

        # EventBridge raises an exception
        mock_events.put_events.side_effect = Exception("EventBridge unavailable")

        event = {
            "shipment_id": "ship-006",
            "origin": "Boston, MA",
            "destination": "Philadelphia, PA",
            "weight_kg": 200,
        }

        result = handler(event, None)

        # Should still succeed — EventBridge is non-blocking
        assert result["carrier_id"] == "carrier-a"
        assert result["rate_usd"] == 1000.0
