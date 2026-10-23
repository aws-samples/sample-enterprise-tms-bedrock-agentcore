"""Create Freight Unit Lambda.

Creates a freight unit record in DynamoDB and publishes a ShipmentCreated
event to EventBridge. This is Step 1 of the shipment lifecycle workflow.

Input: origin, destination, weight_kg, commodity, delivery_date
Output: freight_unit_id, shipment_id, status, created_at
Side effects: writes to DynamoDB, publishes ShipmentCreated event

Idempotency: If an `idempotency_key` is provided, the Lambda will check
DynamoDB for an existing record with that key and return the original
response instead of creating a duplicate shipment.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

from shared.constants import (
    PK_SHIPMENT,
    SK_STATUS,
    TABLE_NAME,
)
from shared.dynamo_utils import get_item, put_item, put_shipment, put_shipment_event
from shared.events import publish_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# DynamoDB key pattern for idempotency records
PK_IDEMPOTENCY = "IDEMPOTENCY#"
SK_IDEMPOTENCY = "CREATE_FREIGHT_UNIT"


def _check_idempotency(idempotency_key: str) -> dict | None:
    """Check if an idempotent request has already been processed.

    Args:
        idempotency_key: Client-provided unique key for this request.

    Returns:
        The cached response dict if found, or None if this is a new request.
    """
    item = get_item(f"{PK_IDEMPOTENCY}{idempotency_key}", SK_IDEMPOTENCY)
    if item and "response" in item:
        logger.info("Idempotency hit for key=%s", idempotency_key)
        return json.loads(item["response"])
    return None


def _store_idempotency(idempotency_key: str, response: dict) -> None:
    """Store the response for idempotent replay.

    Args:
        idempotency_key: Client-provided unique key for this request.
        response: The response to cache for future replays.
    """
    put_item({
        "PK": f"{PK_IDEMPOTENCY}{idempotency_key}",
        "SK": SK_IDEMPOTENCY,
        "response": json.dumps(response),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def handler(event: dict, context: Any) -> dict:
    """Create a freight unit record in DynamoDB.

    Args:
        event: Input payload with origin, destination, weight_kg, commodity, delivery_date.
               Optional: idempotency_key for safe Step Functions retries.
        context: Lambda context object.

    Returns:
        Dict with freight_unit_id, shipment_id, status, and created_at.
    """
    logger.info("CreateFreightUnit invoked: %s", json.dumps(event))

    # Check idempotency — return cached result if this is a retry
    idempotency_key = event.get("idempotency_key")
    if idempotency_key:
        cached = _check_idempotency(idempotency_key)
        if cached:
            return cached

    # Validate required fields
    required_fields = ["origin", "destination", "weight_kg", "commodity", "delivery_date"]
    missing = [f for f in required_fields if f not in event or event[f] is None]
    if missing:
        error_msg = f"Missing required fields: {', '.join(missing)}"
        logger.error(error_msg)
        return {
            "statusCode": 400,
            "error": error_msg,
        }

    # Extract input fields
    origin: str = event["origin"]
    destination: str = event["destination"]
    weight_kg: float = float(event["weight_kg"])
    commodity: str = event["commodity"]
    delivery_date: str = event["delivery_date"]

    # Generate IDs and timestamps
    shipment_id = str(uuid.uuid4())
    freight_unit_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    status = "created"

    # Write shipment record to DynamoDB
    shipment_attributes = {
        "shipment_id": shipment_id,
        "freight_unit_id": freight_unit_id,
        "status": status,
        "origin": origin,
        "destination": destination,
        "weight_kg": str(weight_kg),
        "commodity": commodity,
        "delivery_date": delivery_date,
        "created_at": now,
        "updated_at": now,
    }
    put_shipment(shipment_id, shipment_attributes)
    logger.info("Shipment record created: shipment_id=%s", shipment_id)

    # Store the creation event in DynamoDB
    event_data = {
        "event_type": "ShipmentCreated",
        "shipment_id": shipment_id,
        "freight_unit_id": freight_unit_id,
        "payload": json.dumps({
            "origin": origin,
            "destination": destination,
            "weight_kg": weight_kg,
            "commodity": commodity,
            "delivery_date": delivery_date,
        }),
    }
    put_shipment_event(shipment_id, now, event_data)

    # Publish ShipmentCreated event to EventBridge (standardized envelope)
    publish_event(
        event_type="ShipmentCreated",
        shipment_id=shipment_id,
        step="CreateFreightUnit",
        timestamp=now,
        payload={
            "freight_unit_id": freight_unit_id,
            "origin": origin,
            "destination": destination,
            "weight_kg": weight_kg,
            "commodity": commodity,
            "delivery_date": delivery_date,
        },
    )

    # Build response
    response = {
        "freight_unit_id": freight_unit_id,
        "shipment_id": shipment_id,
        "status": status,
        "created_at": now,
    }

    # Store idempotency record for safe retries
    if idempotency_key:
        _store_idempotency(idempotency_key, response)

    logger.info("CreateFreightUnit response: %s", json.dumps(response))
    return response



