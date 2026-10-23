"""Track Shipment Lambda — tms-track-shipment.

Simulates shipment tracking by updating DynamoDB with an "in_transit" status
and generating a simulated current location between origin and destination.

This Lambda is invoked directly by Step Functions as part of the deterministic
shipment lifecycle workflow.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

# Add project root to path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.constants import PK_SHIPMENT, SK_STATUS
from shared.dynamo_utils import get_shipment, update_shipment

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Simulated city waypoints for common lanes (origin → midpoint approximation)
SIMULATED_LOCATIONS: dict[str, dict[str, float | str]] = {
    "Chicago, IL": {"lat": 41.8781, "lng": -87.6298, "city": "Chicago, IL"},
    "New York, NY": {"lat": 40.7128, "lng": -74.0060, "city": "New York, NY"},
    "Los Angeles, CA": {"lat": 34.0522, "lng": -118.2437, "city": "Los Angeles, CA"},
    "Dallas, TX": {"lat": 32.7767, "lng": -96.7970, "city": "Dallas, TX"},
    "Seattle, WA": {"lat": 47.6062, "lng": -122.3321, "city": "Seattle, WA"},
    "Denver, CO": {"lat": 39.7392, "lng": -104.9903, "city": "Denver, CO"},
    "Atlanta, GA": {"lat": 33.7490, "lng": -84.3880, "city": "Atlanta, GA"},
    "Miami, FL": {"lat": 25.7617, "lng": -80.1918, "city": "Miami, FL"},
    "Boston, MA": {"lat": 42.3601, "lng": -71.0589, "city": "Boston, MA"},
    "Philadelphia, PA": {"lat": 39.9526, "lng": -75.1652, "city": "Philadelphia, PA"},
}


def handler(event: dict, context: Any) -> dict:
    """Track shipment status and simulate in-transit location.

    Args:
        event: Must contain 'shipment_id'. Passed from Step Functions.
        context: Lambda context object.

    Returns:
        Dict with shipment_id, current_status, location (lat, lng, city), and eta.
    """
    logger.info("TrackShipment invoked: %s", json.dumps(event))

    shipment_id: str = event.get("shipment_id", "")
    if not shipment_id:
        logger.error("Missing required field: shipment_id")
        return {
            "statusCode": 400,
            "body": {"error": "Missing required field: shipment_id"},
        }

    # Fetch existing shipment to get origin/destination for location simulation
    shipment = get_shipment(shipment_id)
    if not shipment:
        logger.error("Shipment not found: %s", shipment_id)
        return {
            "statusCode": 404,
            "body": {"error": f"Shipment not found: {shipment_id}"},
        }

    origin: str = shipment.get("origin", "Chicago, IL")
    destination: str = shipment.get("destination", "New York, NY")

    # Simulate a location approximately midway between origin and destination
    location = _simulate_location(origin, destination)

    # Calculate a simulated ETA (24 hours from now for demo purposes)
    eta = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    # Update DynamoDB with in_transit status
    now = datetime.now(timezone.utc).isoformat()
    update_shipment(
        shipment_id,
        {
            "status": "in_transit",
            "current_location": json.dumps(location),
            "eta": eta,
            "updated_at": now,
        },
    )

    logger.info(
        "Shipment %s tracked: status=in_transit, location=%s, eta=%s",
        shipment_id,
        location.get("city"),
        eta,
    )

    # Flat return (consistent with the other workflow tool Lambdas): Step
    # Functions reads these fields directly from $.Payload.<field>.
    return {
        "shipment_id": shipment_id,
        "current_status": "in_transit",
        "location": location,
        "eta": eta,
    }


def _simulate_location(origin: str, destination: str) -> dict[str, float | str]:
    """Generate a simulated location midway between origin and destination.

    Uses pre-defined city coordinates. If a city isn't found, defaults to
    a midpoint approximation based on Chicago and New York.

    Args:
        origin: Origin city string (e.g., "Chicago, IL").
        destination: Destination city string (e.g., "New York, NY").

    Returns:
        Dict with lat, lng, and city name of the simulated current position.
    """
    origin_coords = SIMULATED_LOCATIONS.get(
        origin, {"lat": 41.8781, "lng": -87.6298}
    )
    dest_coords = SIMULATED_LOCATIONS.get(
        destination, {"lat": 40.7128, "lng": -74.0060}
    )

    # Simulate ~60% progress along the route for demo purposes
    progress = 0.6
    mid_lat = float(origin_coords["lat"]) + progress * (
        float(dest_coords["lat"]) - float(origin_coords["lat"])
    )
    mid_lng = float(origin_coords["lng"]) + progress * (
        float(dest_coords["lng"]) - float(origin_coords["lng"])
    )

    # Round to 4 decimal places for readability
    mid_lat = round(mid_lat, 4)
    mid_lng = round(mid_lng, 4)

    # Determine a city label for the midpoint
    city = _nearest_city(mid_lat, mid_lng)

    return {"lat": mid_lat, "lng": mid_lng, "city": city}


def _nearest_city(lat: float, lng: float) -> str:
    """Find the nearest known city to the given coordinates.

    Simple Euclidean distance approximation for demo purposes.

    Args:
        lat: Latitude of the point.
        lng: Longitude of the point.

    Returns:
        Name of the nearest city from SIMULATED_LOCATIONS.
    """
    nearest = "In Transit"
    min_distance = float("inf")

    for city_name, coords in SIMULATED_LOCATIONS.items():
        dist = (float(coords["lat"]) - lat) ** 2 + (float(coords["lng"]) - lng) ** 2
        if dist < min_distance:
            min_distance = dist
            nearest = city_name

    return f"Near {nearest}"
