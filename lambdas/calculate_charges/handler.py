"""Calculate Charges Lambda.

Calculates freight charges for a shipment based on carrier rate data stored
in DynamoDB. This is Step 6 (CalculateCharges) in the shipment lifecycle workflow.

Input: shipment_id, carrier_id, distance_km, weight_kg
Output: base_charge, fuel_surcharge, total_charge, currency
Side effects: updates DynamoDB shipment record with charge breakdown

Charge Calculation Logic:
  - base_charge = rate_usd from the carrier rate record (per-shipment flat rate)
  - fuel_surcharge = 15% of base_charge
  - total_charge = base_charge + fuel_surcharge
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import boto3

from shared.constants import (
    PK_RATE,
    PK_SHIPMENT,
    SK_STATUS,
)
from shared.dynamo_utils import get_item, get_shipment, update_shipment, query_items
from shared.events import publish_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Fuel surcharge percentage (15% of base charge)
FUEL_SURCHARGE_PERCENT = Decimal("0.15")

# Default currency
CURRENCY = "USD"


def handler(event: dict, context: Any) -> dict:
    """Calculate freight charges based on rate data in DynamoDB.

    Args:
        event: Input payload with shipment_id, carrier_id, distance_km, weight_kg.
        context: Lambda context object.

    Returns:
        Dict with base_charge, fuel_surcharge, total_charge, and currency.
    """
    logger.info("CalculateCharges invoked: %s", json.dumps(event))

    # Validate required fields
    required_fields = ["shipment_id", "carrier_id", "distance_km", "weight_kg"]
    missing = [f for f in required_fields if f not in event or event[f] is None]
    if missing:
        error_msg = f"Missing required fields: {', '.join(missing)}"
        logger.error(error_msg)
        return {
            "statusCode": 400,
            "error": error_msg,
        }

    # Extract input fields
    shipment_id: str = event["shipment_id"]
    carrier_id: str = event["carrier_id"]
    distance_km: float = float(event["distance_km"])
    weight_kg: float = float(event["weight_kg"])

    # Validate numeric inputs are positive
    if distance_km <= 0:
        error_msg = "distance_km must be positive"
        logger.error(error_msg)
        return {"statusCode": 400, "error": error_msg}

    if weight_kg <= 0:
        error_msg = "weight_kg must be positive"
        logger.error(error_msg)
        return {"statusCode": 400, "error": error_msg}

    # Look up shipment to get origin/destination for rate lookup
    shipment = get_shipment(shipment_id)
    if not shipment:
        error_msg = f"Shipment not found: {shipment_id}"
        logger.error(error_msg)
        return {"statusCode": 404, "error": error_msg}

    origin = shipment.get("origin", "")
    destination = shipment.get("destination", "")

    if not origin or not destination:
        error_msg = f"Shipment {shipment_id} missing origin/destination"
        logger.error(error_msg)
        return {"statusCode": 400, "error": error_msg}

    # Look up rate record for this origin/destination/carrier
    rate_pk = f"{PK_RATE}{origin}#{destination}"
    rate_sk = f"CARRIER#{carrier_id}"
    rate_record = get_item(rate_pk, rate_sk)

    if not rate_record:
        error_msg = (
            f"No rate found for carrier={carrier_id}, "
            f"origin={origin}, destination={destination}"
        )
        logger.error(error_msg)
        return {"statusCode": 404, "error": error_msg}

    # Calculate charges
    rate_usd = Decimal(str(rate_record["rate_usd"]))
    base_charge = rate_usd
    fuel_surcharge = (base_charge * FUEL_SURCHARGE_PERCENT).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total_charge = base_charge + fuel_surcharge

    # Build charge breakdown
    charges = {
        "base_charge": str(total_as_float(base_charge)),
        "fuel_surcharge": str(total_as_float(fuel_surcharge)),
        "total_charge": str(total_as_float(total_charge)),
        "currency": CURRENCY,
    }

    # Update shipment record with charges
    now = datetime.now(timezone.utc).isoformat()
    update_shipment(shipment_id, {
        "charges": json.dumps(charges),
        "updated_at": now,
    })
    logger.info("Shipment %s updated with charges: %s", shipment_id, json.dumps(charges))

    # Publish ChargesCalculated event to EventBridge (standardized envelope).
    # NOTE: this step previously published "ShipmentCompleted", colliding with
    # the final CompleteShipment step. It now publishes its own event type.
    publish_event(
        event_type="ChargesCalculated",
        shipment_id=shipment_id,
        step="CalculateCharges",
        timestamp=now,
        payload={
            "carrier_id": carrier_id,
            "base_charge": charges["base_charge"],
            "fuel_surcharge": charges["fuel_surcharge"],
            "total_charge": charges["total_charge"],
            "currency": charges["currency"],
        },
    )

    # Build response
    response = {
        "shipment_id": shipment_id,
        "carrier_id": carrier_id,
        "base_charge": float(base_charge),
        "fuel_surcharge": float(fuel_surcharge),
        "total_charge": float(total_charge),
        "currency": CURRENCY,
    }

    logger.info("CalculateCharges response: %s", json.dumps(response))
    return response


def total_as_float(value: Decimal) -> float:
    """Convert a Decimal to float for JSON serialization."""
    return float(value)



