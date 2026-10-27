"""Carrier Rate Simulator Lambda.

Returns simulated carrier rate quotes for a given origin, destination, weight,
and service type. First attempts to query rate records from DynamoDB seed data;
if no exact match is found, generates simulated rates based on weight with
randomized variance.

Requirements: 9.1, 9.2, 9.3
"""

import hashlib
import json
import logging
import os
import random
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

# Random source for simulated quote variance. Not used for any security purpose;
# SystemRandom avoids the predictable default PRNG.
_rng = random.SystemRandom()

from shared.constants import TABLE_NAME, PK_RATE, PK_CARRIER, SK_PROFILE
from shared.dynamo_utils import get_rates, get_carrier

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# Default carriers used for simulated quotes when no DynamoDB records found
_DEFAULT_CARRIERS = [
    {"carrier_id": "carrier-a", "carrier_name": "Carrier A"},
    {"carrier_id": "carrier-b", "carrier_name": "Carrier B"},
    {"carrier_id": "carrier-c", "carrier_name": "Carrier C"},
    {"carrier_id": "carrier-d", "carrier_name": "Carrier D"},
    {"carrier_id": "carrier-e", "carrier_name": "Carrier E"},
]


def handler(event: dict, context: Any) -> dict:
    """Return simulated carrier rate quotes.

    Args:
        event: Dict with keys:
            - origin (str): Origin city and state, e.g. "Chicago, IL"
            - destination (str): Destination city and state, e.g. "New York, NY"
            - weight_kg (float|int): Shipment weight in kilograms
            - service_type (str, optional): Preferred service type filter

    Returns:
        Dict with "quotes" list, each containing:
            carrier_id, carrier_name, rate_usd, transit_days, service_type
    """
    logger.info("Carrier simulator invoked: %s", json.dumps(event, default=str))

    # Validate required fields
    origin = event.get("origin")
    destination = event.get("destination")
    weight_kg = event.get("weight_kg")
    service_type = event.get("service_type")

    if not origin or not destination:
        return _error_response("origin and destination are required")

    if weight_kg is None:
        return _error_response("weight_kg is required")

    try:
        weight_kg = float(weight_kg)
    except (TypeError, ValueError):
        return _error_response("weight_kg must be a number")

    if weight_kg <= 0:
        return _error_response("weight_kg must be positive")

    # Try to get exact rate records from DynamoDB
    quotes = _get_quotes_from_dynamo(origin, destination, weight_kg, service_type)

    # If no DynamoDB records found, generate simulated rates
    if not quotes:
        logger.info(
            "No exact rate records for %s → %s, generating simulated rates",
            origin,
            destination,
        )
        quotes = _generate_simulated_quotes(origin, destination, weight_kg, service_type)

    logger.info("Returning %d quotes for %s → %s", len(quotes), origin, destination)

    return {"quotes": quotes}


def _get_quotes_from_dynamo(
    origin: str,
    destination: str,
    weight_kg: float,
    service_type: str | None,
) -> list[dict]:
    """Query DynamoDB for rate records matching the origin-destination lane.

    Returns a list of quote dicts built from stored rate records.
    """
    rate_records = get_rates(origin, destination)

    if not rate_records:
        return []

    quotes: list[dict] = []
    for record in rate_records:
        # Extract carrier_id from the SK (format: CARRIER#{carrier_id})
        sk = record.get("SK", "")
        carrier_id = sk.replace("CARRIER#", "") if sk.startswith("CARRIER#") else sk

        # Optionally filter by service_type
        record_service_type = record.get("service_type", "ground_freight")
        if service_type and service_type.lower() != record_service_type.lower():
            continue

        # Look up carrier name from carrier profile
        carrier_name = _resolve_carrier_name(carrier_id)

        # Parse rate_usd (stored as string in seed data)
        try:
            rate_usd = float(record.get("rate_usd", "0"))
        except (TypeError, ValueError):
            rate_usd = 0.0

        # Apply weight-based adjustment to the base rate
        rate_usd = _apply_weight_adjustment(rate_usd, weight_kg)

        transit_days = int(record.get("transit_days", 3))

        quotes.append(
            {
                "carrier_id": carrier_id,
                "carrier_name": carrier_name,
                "rate_usd": round(rate_usd, 2),
                "transit_days": transit_days,
                "service_type": record_service_type,
            }
        )

    return quotes


def _generate_simulated_quotes(
    origin: str,
    destination: str,
    weight_kg: float,
    service_type: str | None,
) -> list[dict]:
    """Generate simulated carrier quotes when no DynamoDB records exist.

    Uses a deterministic seed based on origin+destination for reproducible
    base rates, then applies weight-based pricing and randomized variance.
    """
    # Create a deterministic seed from the lane for reproducible base rates.
    # NOTE: MD5 here is NOT used for security — it only derives a stable numeric
    # seed so the same lane always produces the same simulated base rate.
    lane_seed = hashlib.md5(
        f"{origin}#{destination}".encode(), usedforsecurity=False
    ).hexdigest()
    lane_base = int(lane_seed[:8], 16) % 1000 + 500  # Base rate between 500-1500

    quotes: list[dict] = []
    # Pick 2-4 carriers randomly for the simulated lane
    num_carriers = _rng.randint(2, 4)
    selected_carriers = _rng.sample(_DEFAULT_CARRIERS, num_carriers)

    for carrier in selected_carriers:
        # Randomized variance per carrier (+/- 15%)
        variance = _rng.uniform(0.85, 1.15)
        base_rate = lane_base * variance

        # Weight-based pricing
        rate = _apply_weight_adjustment(base_rate, weight_kg)

        # Randomized transit days (1-5)
        transit_days = _rng.randint(1, 5)

        # Assign service type
        carrier_service = service_type or _infer_service_type(carrier["carrier_id"])

        quotes.append(
            {
                "carrier_id": carrier["carrier_id"],
                "carrier_name": carrier["carrier_name"],
                "rate_usd": round(rate, 2),
                "transit_days": transit_days,
                "service_type": carrier_service,
            }
        )

    return quotes


def _apply_weight_adjustment(base_rate: float, weight_kg: float) -> float:
    """Apply weight-based adjustment to the base rate.

    Heavier shipments cost more. Uses a tiered multiplier:
    - 0-100 kg: 0.8x
    - 100-500 kg: 1.0x
    - 500-1000 kg: 1.2x
    - 1000-2000 kg: 1.5x
    - 2000+ kg: 2.0x
    """
    if weight_kg <= 100:
        multiplier = 0.8
    elif weight_kg <= 500:
        multiplier = 1.0
    elif weight_kg <= 1000:
        multiplier = 1.2
    elif weight_kg <= 2000:
        multiplier = 1.5
    else:
        multiplier = 2.0

    return base_rate * multiplier


def _resolve_carrier_name(carrier_id: str) -> str:
    """Look up carrier name from DynamoDB profile. Falls back to formatted ID."""
    try:
        carrier = get_carrier(carrier_id)
        if carrier:
            return carrier.get("name", carrier_id)
    except Exception as e:
        logger.warning("Failed to resolve carrier name for %s: %s", carrier_id, str(e))

    # Fallback: derive name from carrier_id
    name_map = {
        "carrier-a": "Carrier A",
        "carrier-b": "Carrier B",
        "carrier-c": "Carrier C",
        "carrier-d": "Carrier D",
        "carrier-e": "Carrier E",
    }
    return name_map.get(carrier_id, carrier_id)


def _infer_service_type(carrier_id: str) -> str:
    """Infer a default service type based on carrier ID."""
    service_map = {
        "carrier-a": "ground_freight",
        "carrier-b": "express_freight",
        "carrier-c": "ltl_freight",
        "carrier-d": "truckload",
        "carrier-e": "truckload",
    }
    return service_map.get(carrier_id, "ground_freight")


def _error_response(message: str) -> dict:
    """Return a structured error response."""
    logger.error("Carrier simulator error: %s", message)
    return {"error": message, "quotes": []}
