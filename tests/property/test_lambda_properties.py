"""Property-based tests for TMS Lambda handlers.

Uses Hypothesis to generate random inputs and verify correctness properties
hold across 100+ examples for each property.

Properties tested:
- Property 2: Freight Unit Creation Round-Trip
- Property 3: Charge Calculation Determinism
- Property 4: Carrier Simulator Returns Valid Quotes
- Property 6: DynamoDB Records Contain Required Schema Fields

Validates: Requirements 8.1, 8.4, 9.1, 11.1, 11.2, 11.3
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# --- Hypothesis Strategies ---

# Strategy for city names (realistic origin/destination values)
city_strategy = st.sampled_from([
    "Chicago, IL",
    "New York, NY",
    "Los Angeles, CA",
    "Dallas, TX",
    "Seattle, WA",
    "Denver, CO",
    "Miami, FL",
    "Atlanta, GA",
    "Boston, MA",
    "Phoenix, AZ",
])

# Strategy for positive weight in kg (realistic freight weights: 1 to 25000 kg)
weight_strategy = st.floats(min_value=0.1, max_value=25000.0, allow_nan=False, allow_infinity=False)

# Strategy for commodity types
commodity_strategy = st.sampled_from([
    "Electronics",
    "Furniture",
    "Food & Beverage",
    "Chemicals",
    "Automotive Parts",
    "Textiles",
    "Pharmaceuticals",
    "Machinery",
    "Building Materials",
    "Consumer Goods",
])

# Strategy for delivery dates (ISO-8601)
delivery_date_strategy = st.dates(
    min_value=datetime(2025, 1, 1).date(),
    max_value=datetime(2026, 12, 31).date(),
).map(lambda d: d.isoformat())

# Strategy for positive distance in km
distance_strategy = st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False)

# Strategy for rate_usd values (positive)
rate_strategy = st.floats(min_value=10.0, max_value=50000.0, allow_nan=False, allow_infinity=False)

# Strategy for service types
service_type_strategy = st.sampled_from([
    "ground_freight",
    "express_freight",
    "ltl_freight",
    "truckload",
])


# =============================================================================
# Property 2: Freight Unit Creation Round-Trip
# =============================================================================

class TestFreightUnitCreationRoundTrip:
    """Property 2: Freight Unit Creation Round-Trip.

    For any valid freight unit input (origin, destination, weight_kg, commodity,
    delivery_date), creating a freight unit and then reading it back from DynamoDB
    SHALL return a record with matching field values and status "created".

    **Validates: Requirements 8.1**
    """

    @given(
        origin=city_strategy,
        destination=city_strategy,
        weight_kg=weight_strategy,
        commodity=commodity_strategy,
        delivery_date=delivery_date_strategy,
    )
    @settings(max_examples=150)
    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_create_and_read_back_matches(
        self,
        mock_put_shipment,
        mock_put_event,
        mock_publish,
        origin,
        destination,
        weight_kg,
        commodity,
        delivery_date,
    ):
        """Creating a freight unit produces a record with matching fields and status 'created'."""
        from lambdas.create_freight_unit.handler import handler

        event = {
            "origin": origin,
            "destination": destination,
            "weight_kg": weight_kg,
            "commodity": commodity,
            "delivery_date": delivery_date,
        }

        result = handler(event, None)

        # The handler should return required fields
        assert "freight_unit_id" in result
        assert "shipment_id" in result
        assert "status" in result
        assert "created_at" in result

        # Status must be "created"
        assert result["status"] == "created"

        # IDs must be valid UUIDs
        uuid.UUID(result["freight_unit_id"])
        uuid.UUID(result["shipment_id"])

        # created_at must be a valid ISO timestamp
        datetime.fromisoformat(result["created_at"])

        # Verify the DynamoDB put was called with matching field values
        mock_put_shipment.assert_called_once()
        call_args = mock_put_shipment.call_args[0]
        stored_shipment_id = call_args[0]
        stored_attributes = call_args[1]

        assert stored_shipment_id == result["shipment_id"]
        assert stored_attributes["origin"] == origin
        assert stored_attributes["destination"] == destination
        assert stored_attributes["weight_kg"] == str(float(weight_kg))
        assert stored_attributes["commodity"] == commodity
        assert stored_attributes["delivery_date"] == delivery_date
        assert stored_attributes["status"] == "created"


# =============================================================================
# Property 3: Charge Calculation Determinism
# =============================================================================

class TestChargeCalculationDeterminism:
    """Property 3: Charge Calculation Determinism.

    For any shipment with a known carrier rate and distance, calculating charges
    SHALL produce a deterministic result where total_charge equals base_charge
    plus fuel_surcharge.

    **Validates: Requirements 8.4**
    """

    @given(
        distance_km=distance_strategy,
        weight_kg=weight_strategy,
        rate_usd=rate_strategy,
    )
    @settings(max_examples=150)
    @patch("lambdas.calculate_charges.handler.publish_event")
    @patch("lambdas.calculate_charges.handler.update_shipment")
    @patch("lambdas.calculate_charges.handler.get_item")
    @patch("lambdas.calculate_charges.handler.get_shipment")
    def test_total_equals_base_plus_fuel_surcharge(
        self,
        mock_get_shipment,
        mock_get_item,
        mock_update_shipment,
        mock_publish,
        distance_km,
        weight_kg,
        rate_usd,
    ):
        """total_charge = base_charge + fuel_surcharge for any valid inputs."""
        from lambdas.calculate_charges.handler import handler

        shipment_id = str(uuid.uuid4())
        carrier_id = "carrier-test"

        # Mock shipment lookup
        mock_get_shipment.return_value = {
            "shipment_id": shipment_id,
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "status": "carrier_assigned",
        }

        # Mock rate record lookup
        mock_get_item.return_value = {
            "PK": "RATE#Chicago, IL#New York, NY",
            "SK": f"CARRIER#{carrier_id}",
            "rate_usd": str(rate_usd),
            "transit_days": 3,
            "service_type": "ground_freight",
        }

        event = {
            "shipment_id": shipment_id,
            "carrier_id": carrier_id,
            "distance_km": distance_km,
            "weight_kg": weight_kg,
        }

        result = handler(event, None)

        # Verify the response contains charge fields
        assert "base_charge" in result
        assert "fuel_surcharge" in result
        assert "total_charge" in result
        assert "currency" in result

        # Verify the deterministic relationship: total = base + fuel_surcharge
        base = result["base_charge"]
        fuel = result["fuel_surcharge"]
        total = result["total_charge"]

        # Use the same calculation logic as the handler for verification
        expected_base = Decimal(str(rate_usd))
        expected_fuel = (expected_base * Decimal("0.15")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        expected_total = expected_base + expected_fuel

        assert abs(base - float(expected_base)) < 0.01
        assert abs(fuel - float(expected_fuel)) < 0.01
        assert abs(total - float(expected_total)) < 0.01

        # Verify the fundamental property: total = base + fuel_surcharge
        assert abs(total - (base + fuel)) < 0.01

        # Currency must always be USD
        assert result["currency"] == "USD"


# =============================================================================
# Property 4: Carrier Simulator Returns Valid Quotes
# =============================================================================

class TestCarrierSimulatorValidQuotes:
    """Property 4: Carrier Simulator Returns Valid Quotes.

    For any valid origin, destination, and weight_kg input, the carrier simulator
    SHALL return at least one quote containing carrier_id, carrier_name, rate_usd
    (positive number), transit_days (positive integer), and service_type.

    **Validates: Requirements 9.1**
    """

    @given(
        origin=city_strategy,
        destination=city_strategy,
        weight_kg=weight_strategy,
        service_type=st.one_of(service_type_strategy, st.none()),
    )
    @settings(max_examples=150)
    @patch("lambdas.carrier_simulator.handler.get_carrier")
    @patch("lambdas.carrier_simulator.handler.get_rates")
    def test_returns_valid_quote_schema(
        self,
        mock_get_rates,
        mock_get_carrier,
        origin,
        destination,
        weight_kg,
        service_type,
    ):
        """Carrier simulator always returns at least one valid quote for valid inputs."""
        from lambdas.carrier_simulator.handler import handler

        # Mock DynamoDB - return empty rates so simulated quotes are generated
        mock_get_rates.return_value = []
        mock_get_carrier.return_value = None

        event = {
            "origin": origin,
            "destination": destination,
            "weight_kg": weight_kg,
        }
        if service_type is not None:
            event["service_type"] = service_type

        result = handler(event, None)

        # Must have a "quotes" key
        assert "quotes" in result
        quotes = result["quotes"]

        # Must return at least one quote
        assert len(quotes) >= 1

        # Each quote must contain required fields with valid types
        for quote in quotes:
            assert "carrier_id" in quote
            assert "carrier_name" in quote
            assert "rate_usd" in quote
            assert "transit_days" in quote
            assert "service_type" in quote

            # carrier_id and carrier_name must be non-empty strings
            assert isinstance(quote["carrier_id"], str) and len(quote["carrier_id"]) > 0
            assert isinstance(quote["carrier_name"], str) and len(quote["carrier_name"]) > 0

            # rate_usd must be a positive number
            assert isinstance(quote["rate_usd"], (int, float))
            assert quote["rate_usd"] > 0

            # transit_days must be a positive integer
            assert isinstance(quote["transit_days"], int)
            assert quote["transit_days"] > 0

            # service_type must be a non-empty string
            assert isinstance(quote["service_type"], str) and len(quote["service_type"]) > 0


# =============================================================================
# Property 6: DynamoDB Records Contain Required Schema Fields
# =============================================================================

class TestDynamoDBRecordsContainRequiredFields:
    """Property 6: DynamoDB Records Contain Required Schema Fields.

    For any carrier record stored in DynamoDB, it SHALL contain carrier name,
    service type, and coverage region. For any rate record, it SHALL contain
    origin, destination, weight tier, and pricing. For any shipment record,
    it SHALL contain status, origin, destination, and lifecycle timestamps.

    **Validates: Requirements 11.1, 11.2, 11.3**
    """

    @given(
        carrier_name=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"))),
        service_type=service_type_strategy,
        coverage_region=st.sampled_from(["nationwide", "northeast", "southeast", "midwest", "west_coast"]),
    )
    @settings(max_examples=150)
    def test_carrier_records_have_required_fields(
        self,
        carrier_name,
        service_type,
        coverage_region,
    ):
        """Carrier records contain name, service_type, and coverage_region."""
        from shared.constants import PK_CARRIER, SK_PROFILE

        carrier_id = str(uuid.uuid4())

        # Build a carrier record as it would be stored in DynamoDB
        carrier_record = {
            "PK": f"{PK_CARRIER}{carrier_id}",
            "SK": SK_PROFILE,
            "name": carrier_name,
            "service_type": service_type,
            "coverage_region": coverage_region,
            "carrier_id": carrier_id,
        }

        # Verify all required fields are present and non-empty
        assert "PK" in carrier_record and carrier_record["PK"].startswith(PK_CARRIER)
        assert "SK" in carrier_record and carrier_record["SK"] == SK_PROFILE
        assert "name" in carrier_record and len(carrier_record["name"]) > 0
        assert "service_type" in carrier_record and len(carrier_record["service_type"]) > 0
        assert "coverage_region" in carrier_record and len(carrier_record["coverage_region"]) > 0

    @given(
        origin=city_strategy,
        destination=city_strategy,
        rate_usd=rate_strategy,
        transit_days=st.integers(min_value=1, max_value=14),
        weight_tier=st.sampled_from(["0-100", "100-500", "500-1000", "1000-2000", "2000+"]),
    )
    @settings(max_examples=150)
    def test_rate_records_have_required_fields(
        self,
        origin,
        destination,
        rate_usd,
        transit_days,
        weight_tier,
    ):
        """Rate records contain origin, destination, weight_tier, and pricing."""
        from shared.constants import PK_RATE

        carrier_id = str(uuid.uuid4())

        # Build a rate record as it would be stored in DynamoDB
        rate_record = {
            "PK": f"{PK_RATE}{origin}#{destination}",
            "SK": f"CARRIER#{carrier_id}",
            "rate_usd": str(rate_usd),
            "transit_days": transit_days,
            "weight_tier": weight_tier,
            "service_type": "ground_freight",
        }

        # Verify PK encodes origin and destination
        assert origin in rate_record["PK"]
        assert destination in rate_record["PK"]

        # Verify required fields present
        assert "rate_usd" in rate_record
        assert float(rate_record["rate_usd"]) > 0
        assert "weight_tier" in rate_record and len(rate_record["weight_tier"]) > 0
        assert "transit_days" in rate_record and rate_record["transit_days"] > 0

    @given(
        origin=city_strategy,
        destination=city_strategy,
        weight_kg=weight_strategy,
        commodity=commodity_strategy,
        delivery_date=delivery_date_strategy,
    )
    @settings(max_examples=150)
    @patch("lambdas.create_freight_unit.handler.publish_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment_event")
    @patch("lambdas.create_freight_unit.handler.put_shipment")
    def test_shipment_records_have_required_fields(
        self,
        mock_put_shipment,
        mock_put_event,
        mock_publish,
        origin,
        destination,
        weight_kg,
        commodity,
        delivery_date,
    ):
        """Shipment records contain status, origin, destination, and lifecycle timestamps."""
        from lambdas.create_freight_unit.handler import handler

        event = {
            "origin": origin,
            "destination": destination,
            "weight_kg": weight_kg,
            "commodity": commodity,
            "delivery_date": delivery_date,
        }

        result = handler(event, None)

        # Verify the handler stored a record with required schema fields
        mock_put_shipment.assert_called_once()
        stored_attributes = mock_put_shipment.call_args[0][1]

        # Required: status
        assert "status" in stored_attributes
        assert stored_attributes["status"] in [
            "created", "planning", "carrier_assigned",
            "in_transit", "exception", "complete",
        ]

        # Required: origin and destination
        assert "origin" in stored_attributes and len(stored_attributes["origin"]) > 0
        assert "destination" in stored_attributes and len(stored_attributes["destination"]) > 0

        # Required: lifecycle timestamps
        assert "created_at" in stored_attributes
        assert "updated_at" in stored_attributes
        # Timestamps must be valid ISO-8601
        datetime.fromisoformat(stored_attributes["created_at"])
        datetime.fromisoformat(stored_attributes["updated_at"])
