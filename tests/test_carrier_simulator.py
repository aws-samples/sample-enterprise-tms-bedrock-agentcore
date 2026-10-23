"""Unit tests for the carrier simulator Lambda handler."""

import sys
import os
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lambdas.carrier_simulator.handler import (
    handler,
    _apply_weight_adjustment,
    _generate_simulated_quotes,
    _resolve_carrier_name,
    _get_quotes_from_dynamo,
)


class TestWeightAdjustment:
    """Tests for _apply_weight_adjustment."""

    def test_light_weight_tier(self):
        result = _apply_weight_adjustment(1000.0, 50)
        assert result == 800.0  # 0.8x

    def test_medium_weight_tier(self):
        result = _apply_weight_adjustment(1000.0, 300)
        assert result == 1000.0  # 1.0x

    def test_heavy_weight_tier(self):
        result = _apply_weight_adjustment(1000.0, 750)
        assert result == 1200.0  # 1.2x

    def test_very_heavy_weight_tier(self):
        result = _apply_weight_adjustment(1000.0, 1500)
        assert result == 1500.0  # 1.5x

    def test_extra_heavy_weight_tier(self):
        result = _apply_weight_adjustment(1000.0, 3000)
        assert result == 2000.0  # 2.0x

    def test_boundary_100kg(self):
        result = _apply_weight_adjustment(1000.0, 100)
        assert result == 800.0  # 0.8x (<=100)

    def test_boundary_500kg(self):
        result = _apply_weight_adjustment(1000.0, 500)
        assert result == 1000.0  # 1.0x (<=500)


class TestHandlerValidation:
    """Tests for input validation in the handler."""

    def test_missing_origin(self):
        result = handler({"destination": "NYC", "weight_kg": 100}, None)
        assert "error" in result
        assert result["quotes"] == []

    def test_missing_destination(self):
        result = handler({"origin": "Chicago", "weight_kg": 100}, None)
        assert "error" in result
        assert result["quotes"] == []

    def test_missing_weight(self):
        result = handler({"origin": "Chicago", "destination": "NYC"}, None)
        assert "error" in result
        assert result["quotes"] == []

    def test_invalid_weight_string(self):
        result = handler(
            {"origin": "Chicago", "destination": "NYC", "weight_kg": "abc"}, None
        )
        assert "error" in result

    def test_negative_weight(self):
        result = handler(
            {"origin": "Chicago", "destination": "NYC", "weight_kg": -10}, None
        )
        assert "error" in result

    def test_zero_weight(self):
        result = handler(
            {"origin": "Chicago", "destination": "NYC", "weight_kg": 0}, None
        )
        assert "error" in result


class TestGetQuotesFromDynamo:
    """Tests for DynamoDB-based quote retrieval."""

    @patch("lambdas.carrier_simulator.handler.get_carrier")
    @patch("lambdas.carrier_simulator.handler.get_rates")
    def test_returns_quotes_from_dynamo(self, mock_get_rates, mock_get_carrier):
        mock_get_rates.return_value = [
            {
                "PK": "RATE#Chicago, IL#New York, NY",
                "SK": "CARRIER#carrier-ups",
                "rate_usd": "1245.00",
                "transit_days": 3,
                "service_type": "ground_freight",
            },
            {
                "PK": "RATE#Chicago, IL#New York, NY",
                "SK": "CARRIER#carrier-fedex",
                "rate_usd": "1320.00",
                "transit_days": 2,
                "service_type": "express_freight",
            },
        ]
        mock_get_carrier.return_value = {"name": "UPS"}

        quotes = _get_quotes_from_dynamo("Chicago, IL", "New York, NY", 750.0, None)

        assert len(quotes) == 2
        assert quotes[0]["carrier_id"] == "carrier-ups"
        assert quotes[0]["rate_usd"] == 1494.0  # 1245 * 1.2 (500-1000kg tier)
        assert quotes[0]["transit_days"] == 3
        assert quotes[0]["service_type"] == "ground_freight"

    @patch("lambdas.carrier_simulator.handler.get_rates")
    def test_returns_empty_when_no_rates(self, mock_get_rates):
        mock_get_rates.return_value = []

        quotes = _get_quotes_from_dynamo("Unknown, XX", "Nowhere, YY", 500.0, None)
        assert quotes == []

    @patch("lambdas.carrier_simulator.handler.get_carrier")
    @patch("lambdas.carrier_simulator.handler.get_rates")
    def test_filters_by_service_type(self, mock_get_rates, mock_get_carrier):
        mock_get_rates.return_value = [
            {
                "PK": "RATE#Chicago, IL#New York, NY",
                "SK": "CARRIER#carrier-ups",
                "rate_usd": "1245.00",
                "transit_days": 3,
                "service_type": "ground_freight",
            },
            {
                "PK": "RATE#Chicago, IL#New York, NY",
                "SK": "CARRIER#carrier-fedex",
                "rate_usd": "1320.00",
                "transit_days": 2,
                "service_type": "express_freight",
            },
        ]
        mock_get_carrier.return_value = {"name": "FedEx"}

        quotes = _get_quotes_from_dynamo(
            "Chicago, IL", "New York, NY", 750.0, "express_freight"
        )

        assert len(quotes) == 1
        assert quotes[0]["carrier_id"] == "carrier-fedex"


class TestGenerateSimulatedQuotes:
    """Tests for simulated quote generation."""

    def test_returns_at_least_two_quotes(self):
        quotes = _generate_simulated_quotes(
            "Unknown, XX", "Nowhere, YY", 500.0, None
        )
        assert len(quotes) >= 2

    def test_quotes_have_required_fields(self):
        quotes = _generate_simulated_quotes(
            "Unknown, XX", "Nowhere, YY", 500.0, None
        )
        for quote in quotes:
            assert "carrier_id" in quote
            assert "carrier_name" in quote
            assert "rate_usd" in quote
            assert "transit_days" in quote
            assert "service_type" in quote
            assert quote["rate_usd"] > 0
            assert quote["transit_days"] >= 1

    def test_uses_provided_service_type(self):
        quotes = _generate_simulated_quotes(
            "Unknown, XX", "Nowhere, YY", 500.0, "express_freight"
        )
        for quote in quotes:
            assert quote["service_type"] == "express_freight"


class TestHandlerEndToEnd:
    """End-to-end handler tests with mocked DynamoDB."""

    @patch("lambdas.carrier_simulator.handler.get_carrier")
    @patch("lambdas.carrier_simulator.handler.get_rates")
    def test_handler_with_dynamo_rates(self, mock_get_rates, mock_get_carrier):
        mock_get_rates.return_value = [
            {
                "PK": "RATE#Chicago, IL#New York, NY",
                "SK": "CARRIER#carrier-ups",
                "rate_usd": "1245.00",
                "transit_days": 3,
                "service_type": "ground_freight",
            },
        ]
        mock_get_carrier.return_value = {"name": "UPS"}

        result = handler(
            {
                "origin": "Chicago, IL",
                "destination": "New York, NY",
                "weight_kg": 750,
            },
            None,
        )

        assert "quotes" in result
        assert len(result["quotes"]) == 1
        assert result["quotes"][0]["carrier_name"] == "UPS"

    @patch("lambdas.carrier_simulator.handler.get_rates")
    def test_handler_falls_back_to_simulated(self, mock_get_rates):
        mock_get_rates.return_value = []

        result = handler(
            {
                "origin": "Random City, XX",
                "destination": "Other City, YY",
                "weight_kg": 500,
            },
            None,
        )

        assert "quotes" in result
        assert len(result["quotes"]) >= 2
        # All quotes should have valid structure
        for quote in result["quotes"]:
            assert quote["rate_usd"] > 0
            assert quote["transit_days"] >= 1

    @patch("lambdas.carrier_simulator.handler.get_carrier")
    @patch("lambdas.carrier_simulator.handler.get_rates")
    def test_handler_weight_as_string(self, mock_get_rates, mock_get_carrier):
        """Verify weight_kg can be passed as a string (from JSON)."""
        mock_get_rates.return_value = [
            {
                "PK": "RATE#Atlanta, GA#Miami, FL",
                "SK": "CARRIER#carrier-ups",
                "rate_usd": "720.00",
                "transit_days": 2,
                "service_type": "ground_freight",
            },
        ]
        mock_get_carrier.return_value = {"name": "UPS"}

        result = handler(
            {
                "origin": "Atlanta, GA",
                "destination": "Miami, FL",
                "weight_kg": "150",
            },
            None,
        )

        assert "quotes" in result
        assert len(result["quotes"]) == 1
        # 720 * 1.0 (100-500kg tier) = 720.0
        assert result["quotes"][0]["rate_usd"] == 720.0
