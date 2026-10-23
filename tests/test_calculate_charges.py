"""Unit tests for the calculate_charges Lambda handler."""

import json
import os
import sys
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lambdas.calculate_charges.handler import handler, FUEL_SURCHARGE_PERCENT


# --- Fixtures ---

VALID_INPUT = {
    "shipment_id": "ship-001",
    "carrier_id": "carrier-ups",
    "distance_km": 1200.0,
    "weight_kg": 750.0,
}

SAMPLE_SHIPMENT = {
    "PK": "SHIPMENT#ship-001",
    "SK": "STATUS",
    "shipment_id": "ship-001",
    "status": "carrier_assigned",
    "origin": "Chicago, IL",
    "destination": "New York, NY",
    "weight_kg": "750",
    "commodity": "Electronics",
}

SAMPLE_RATE = {
    "PK": "RATE#Chicago, IL#New York, NY",
    "SK": "CARRIER#carrier-ups",
    "rate_usd": "1245.00",
    "transit_days": 3,
    "weight_tier": "500-1000kg",
    "service_type": "ground_freight",
}


class TestInputValidation:
    """Tests for required field validation."""

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_missing_shipment_id(self, mock_get_shipment):
        event = {**VALID_INPUT}
        del event["shipment_id"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "shipment_id" in result["error"]
        mock_get_shipment.assert_not_called()

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_missing_carrier_id(self, mock_get_shipment):
        event = {**VALID_INPUT}
        del event["carrier_id"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "carrier_id" in result["error"]
        mock_get_shipment.assert_not_called()

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_missing_distance_km(self, mock_get_shipment):
        event = {**VALID_INPUT}
        del event["distance_km"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "distance_km" in result["error"]
        mock_get_shipment.assert_not_called()

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_missing_weight_kg(self, mock_get_shipment):
        event = {**VALID_INPUT}
        del event["weight_kg"]
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "weight_kg" in result["error"]
        mock_get_shipment.assert_not_called()

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_multiple_missing_fields(self, mock_get_shipment):
        event = {"shipment_id": "ship-001"}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "carrier_id" in result["error"]
        assert "distance_km" in result["error"]
        assert "weight_kg" in result["error"]
        mock_get_shipment.assert_not_called()

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_null_field_treated_as_missing(self, mock_get_shipment):
        event = {**VALID_INPUT, "carrier_id": None}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "carrier_id" in result["error"]
        mock_get_shipment.assert_not_called()

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_negative_distance_km(self, mock_get_shipment):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        event = {**VALID_INPUT, "distance_km": -100}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "distance_km" in result["error"]

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_zero_distance_km(self, mock_get_shipment):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        event = {**VALID_INPUT, "distance_km": 0}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "distance_km" in result["error"]

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_negative_weight_kg(self, mock_get_shipment):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        event = {**VALID_INPUT, "weight_kg": -50}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "weight_kg" in result["error"]

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_zero_weight_kg(self, mock_get_shipment):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        event = {**VALID_INPUT, "weight_kg": 0}
        result = handler(event, None)
        assert result["statusCode"] == 400
        assert "weight_kg" in result["error"]


class TestShipmentLookup:
    """Tests for shipment record lookup."""

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_shipment_not_found(self, mock_get_shipment):
        mock_get_shipment.return_value = None
        result = handler(VALID_INPUT, None)
        assert result["statusCode"] == 404
        assert "Shipment not found" in result["error"]

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_shipment_missing_origin(self, mock_get_shipment):
        shipment = {**SAMPLE_SHIPMENT, "origin": ""}
        mock_get_shipment.return_value = shipment
        result = handler(VALID_INPUT, None)
        assert result["statusCode"] == 400
        assert "origin/destination" in result["error"]

    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_shipment_missing_destination(self, mock_get_shipment):
        shipment = {**SAMPLE_SHIPMENT, "destination": ""}
        mock_get_shipment.return_value = shipment
        result = handler(VALID_INPUT, None)
        assert result["statusCode"] == 400
        assert "origin/destination" in result["error"]


class TestRateLookup:
    """Tests for carrier rate lookup."""

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_rate_not_found(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = None
        result = handler(VALID_INPUT, None)
        assert result["statusCode"] == 404
        assert "No rate found" in result["error"]
        mock_update.assert_not_called()


class TestChargeCalculation:
    """Tests for the charge calculation logic."""

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_correct_base_charge(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        result = handler(VALID_INPUT, None)
        assert result["base_charge"] == 1245.00

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_fuel_surcharge_is_15_percent(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        result = handler(VALID_INPUT, None)
        expected_surcharge = 1245.00 * 0.15  # 186.75
        assert result["fuel_surcharge"] == pytest.approx(expected_surcharge, abs=0.01)

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_total_equals_base_plus_surcharge(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        """Property 3: total_charge = base_charge + fuel_surcharge."""
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        result = handler(VALID_INPUT, None)
        assert result["total_charge"] == pytest.approx(
            result["base_charge"] + result["fuel_surcharge"], abs=0.01
        )

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_currency_is_usd(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        result = handler(VALID_INPUT, None)
        assert result["currency"] == "USD"

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_returns_shipment_and_carrier_ids(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        result = handler(VALID_INPUT, None)
        assert result["shipment_id"] == "ship-001"
        assert result["carrier_id"] == "carrier-ups"

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_known_values(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        """Verify exact calculations: rate=1245 → base=1245, surcharge=186.75, total=1431.75."""
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        result = handler(VALID_INPUT, None)
        assert result["base_charge"] == 1245.00
        assert result["fuel_surcharge"] == 186.75
        assert result["total_charge"] == 1431.75

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_different_rate(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        """Verify calculation with a different rate value."""
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        rate = {**SAMPLE_RATE, "rate_usd": "1000.00"}
        mock_get_item.return_value = rate
        result = handler(VALID_INPUT, None)
        assert result["base_charge"] == 1000.00
        assert result["fuel_surcharge"] == 150.00
        assert result["total_charge"] == 1150.00

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_string_inputs_for_numeric_fields(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        """Verify that numeric fields passed as strings are handled correctly."""
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        event = {**VALID_INPUT, "distance_km": "1200.5", "weight_kg": "750.0"}
        result = handler(event, None)
        assert result["total_charge"] == 1431.75


class TestDynamoDBUpdate:
    """Tests for DynamoDB shipment record update."""

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_updates_shipment_with_charges(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        handler(VALID_INPUT, None)

        mock_update.assert_called_once()
        args = mock_update.call_args
        assert args[0][0] == "ship-001"
        updates = args[0][1]
        assert "charges" in updates
        assert "updated_at" in updates

        charges = json.loads(updates["charges"])
        assert charges["base_charge"] == "1245.0"
        assert charges["fuel_surcharge"] == "186.75"
        assert charges["total_charge"] == "1431.75"
        assert charges["currency"] == "USD"

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_no_update_when_rate_not_found(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = None
        handler(VALID_INPUT, None)
        mock_update.assert_not_called()


class TestEventPublishing:
    """Tests for EventBridge event publishing."""

    @patch("shared.events._client")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_publishes_correct_event_structure(
        self, mock_get_shipment, mock_get_item, mock_update, mock_client
    ):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        mock_events = MagicMock()
        mock_client.return_value = mock_events
        mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

        handler(VALID_INPUT, None)

        mock_events.put_events.assert_called_once()
        entries = mock_events.put_events.call_args[1]["Entries"]
        assert len(entries) == 1

        entry = entries[0]
        assert entry["Source"] == "tms.mvp"
        # Bug fix: this step now publishes its own event type, not "ShipmentCompleted".
        assert entry["DetailType"] == "ChargesCalculated"

        detail = json.loads(entry["Detail"])
        assert detail["event_type"] == "ChargesCalculated"
        assert detail["shipment_id"] == "ship-001"
        assert detail["step"] == "CalculateCharges"
        # Deterministic step — no AI metadata.
        assert detail["model_tier"] is None
        assert detail["confidence"] is None
        assert detail["payload"]["carrier_id"] == "carrier-ups"
        assert detail["payload"]["total_charge"] == "1431.75"

    @patch("shared.events._client")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_event_publish_failure_does_not_raise(
        self, mock_get_shipment, mock_get_item, mock_update, mock_client
    ):
        """EventBridge publish failures are logged but don't fail the Lambda."""
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        mock_events = MagicMock()
        mock_client.return_value = mock_events
        mock_events.put_events.side_effect = Exception("EventBridge unavailable")

        # Should not raise
        result = handler(VALID_INPUT, None)
        assert result["total_charge"] == 1431.75

    @patch("shared.events._client")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_event_publish_uses_env_bus_name(
        self, mock_get_shipment, mock_get_item, mock_update, mock_client
    ):
        """Uses EVENT_BUS_NAME env var if set."""
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        mock_events = MagicMock()
        mock_client.return_value = mock_events
        mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": []}

        with patch.dict(os.environ, {"EVENT_BUS_NAME": "custom-bus"}):
            handler(VALID_INPUT, None)

        entries = mock_events.put_events.call_args[1]["Entries"]
        assert entries[0]["EventBusName"] == "custom-bus"

    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_handler_publishes_event_on_success(self, mock_get_shipment, mock_get_item, mock_update, mock_publish):
        mock_get_shipment.return_value = SAMPLE_SHIPMENT
        mock_get_item.return_value = SAMPLE_RATE
        handler(VALID_INPUT, None)
        mock_publish.assert_called_once()
        kwargs = mock_publish.call_args[1]
        assert kwargs["event_type"] == "ChargesCalculated"
        assert kwargs["shipment_id"] == "ship-001"
        assert kwargs["step"] == "CalculateCharges"
        assert kwargs["payload"]["carrier_id"] == "carrier-ups"
        assert kwargs["payload"]["total_charge"] == "1431.75"
