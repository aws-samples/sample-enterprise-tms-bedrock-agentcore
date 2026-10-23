"""Unit tests for the tms-track-shipment Lambda handler."""

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.track_shipment.handler import (
    _nearest_city,
    _simulate_location,
    handler,
)


class TestSimulateLocation:
    """Tests for the _simulate_location helper."""

    def test_known_origin_destination_returns_midpoint(self):
        """Simulated location is between origin and destination."""
        location = _simulate_location("Chicago, IL", "New York, NY")
        assert "lat" in location
        assert "lng" in location
        assert "city" in location
        # Midpoint latitude should be between Chicago (41.87) and NYC (40.71)
        assert 40.0 <= location["lat"] <= 42.0
        # Midpoint longitude should be between Chicago (-87.63) and NYC (-74.01)
        assert -88.0 <= location["lng"] <= -74.0

    def test_unknown_cities_use_defaults(self):
        """Unknown cities fall back to Chicago/NYC defaults."""
        location = _simulate_location("Unknown City", "Another City")
        assert "lat" in location
        assert "lng" in location
        assert "city" in location
        assert isinstance(location["lat"], float)
        assert isinstance(location["lng"], float)

    def test_same_origin_destination(self):
        """Same origin and destination produces location near that city."""
        location = _simulate_location("Denver, CO", "Denver, CO")
        # Should be very close to Denver coordinates
        assert abs(location["lat"] - 39.7392) < 1.0
        assert abs(location["lng"] - (-104.9903)) < 1.0


class TestNearestCity:
    """Tests for the _nearest_city helper."""

    def test_exact_city_coordinates(self):
        """Exact city coordinates return that city."""
        result = _nearest_city(41.8781, -87.6298)
        assert "Chicago" in result

    def test_near_destination(self):
        """Coordinates near NYC return NYC."""
        result = _nearest_city(40.7, -74.0)
        assert "New York" in result

    def test_returns_near_prefix(self):
        """Result always starts with 'Near'."""
        result = _nearest_city(35.0, -90.0)
        assert result.startswith("Near ")


class TestHandler:
    """Tests for the track_shipment handler function."""

    def test_missing_shipment_id_returns_400(self):
        """Handler returns 400 when shipment_id is missing."""
        result = handler({}, None)
        assert result["statusCode"] == 400
        assert "error" in result["body"]

    @patch("lambdas.track_shipment.handler.get_shipment")
    def test_shipment_not_found_returns_404(self, mock_get):
        """Handler returns 404 when shipment doesn't exist."""
        mock_get.return_value = None
        result = handler({"shipment_id": "nonexistent-id"}, None)
        assert result["statusCode"] == 404
        assert "not found" in result["body"]["error"].lower()

    @patch("lambdas.track_shipment.handler.update_shipment")
    @patch("lambdas.track_shipment.handler.get_shipment")
    def test_successful_tracking(self, mock_get, mock_update):
        """Handler returns tracking info for a valid shipment."""
        mock_get.return_value = {
            "shipment_id": "test-ship-001",
            "status": "carrier_assigned",
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": "750",
        }
        mock_update.return_value = {}

        result = handler({"shipment_id": "test-ship-001"}, None)

        # Flat success shape (consumed directly by Step Functions).
        assert result["shipment_id"] == "test-ship-001"
        assert result["current_status"] == "in_transit"
        assert "lat" in result["location"]
        assert "lng" in result["location"]
        assert "city" in result["location"]
        assert "eta" in result
        # ETA should be a valid ISO-8601 timestamp
        datetime.fromisoformat(result["eta"])

    @patch("lambdas.track_shipment.handler.update_shipment")
    @patch("lambdas.track_shipment.handler.get_shipment")
    def test_dynamo_updated_with_in_transit(self, mock_get, mock_update):
        """Handler updates DynamoDB with in_transit status."""
        mock_get.return_value = {
            "shipment_id": "test-ship-002",
            "status": "carrier_assigned",
            "origin": "Seattle, WA",
            "destination": "Denver, CO",
        }
        mock_update.return_value = {}

        handler({"shipment_id": "test-ship-002"}, None)

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][0] == "test-ship-002"
        updates = call_args[0][1]
        assert updates["status"] == "in_transit"
        assert "eta" in updates
        assert "current_location" in updates
        assert "updated_at" in updates

    @patch("lambdas.track_shipment.handler.update_shipment")
    @patch("lambdas.track_shipment.handler.get_shipment")
    def test_location_has_correct_structure(self, mock_get, mock_update):
        """Location object has lat, lng, and city fields."""
        mock_get.return_value = {
            "shipment_id": "test-ship-003",
            "origin": "Atlanta, GA",
            "destination": "Miami, FL",
        }
        mock_update.return_value = {}

        result = handler({"shipment_id": "test-ship-003"}, None)

        location = result["location"]
        assert isinstance(location["lat"], float)
        assert isinstance(location["lng"], float)
        assert isinstance(location["city"], str)
        assert location["city"].startswith("Near ")
