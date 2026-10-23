"""Assign Carrier Lambda.

Invokes the carrier simulator to get rate quotes, selects the lowest-rate
carrier, updates the DynamoDB shipment record, and publishes a
CarrierAssigned event to EventBridge. This is Step 3 of the shipment
lifecycle workflow.

Input: shipment_id, origin, destination, weight_kg
Output: carrier_id, carrier_name, rate_usd, service_type
Side effects: invokes carrier simulator, updates DynamoDB, publishes CarrierAssigned event

Idempotency: If an `idempotency_key` is provided, the Lambda will check
DynamoDB for an existing record with that key and return the original
response instead of re-assigning a carrier.

Requirements: 8.2, 10.3
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

from shared.constants import (
    TABLE_NAME,
)
from shared.dynamo_utils import get_item, put_item, update_shipment, put_shipment_event
from shared.events import publish_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# DynamoDB key pattern for idempotency records
PK_IDEMPOTENCY = "IDEMPOTENCY#"
SK_IDEMPOTENCY = "ASSIGN_CARRIER"

# Environment variable for the carrier simulator function name
CARRIER_SIMULATOR_FUNCTION = "tms-carrier-simulator"


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


def _invoke_carrier_simulator(
    origin: str, destination: str, weight_kg: float
) -> list[dict]:
    """Invoke the carrier simulator Lambda to get rate quotes.

    Args:
        origin: Origin city and state.
        destination: Destination city and state.
        weight_kg: Shipment weight in kilograms.

    Returns:
        List of carrier quote dicts from the simulator.

    Raises:
        RuntimeError: If the carrier simulator returns an error or no quotes.
    """
    function_name = os.environ.get(
        "CARRIER_SIMULATOR_FUNCTION", CARRIER_SIMULATOR_FUNCTION
    )

    payload = {
        "origin": origin,
        "destination": destination,
        "weight_kg": weight_kg,
    }

    client = boto3.client("lambda")
    response = client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )

    response_payload = json.loads(response["Payload"].read())
    logger.info(
        "Carrier simulator response: %s", json.dumps(response_payload, default=str)
    )

    if "error" in response_payload and response_payload["error"]:
        raise RuntimeError(
            f"Carrier simulator error: {response_payload['error']}"
        )

    quotes = response_payload.get("quotes", [])
    if not quotes:
        raise RuntimeError("Carrier simulator returned no quotes")

    return quotes


def _select_lowest_rate(quotes: list[dict]) -> dict:
    """Select the carrier with the lowest rate from the quotes.

    Args:
        quotes: List of carrier quote dicts with rate_usd field.

    Returns:
        The quote dict with the lowest rate_usd.
    """
    return min(quotes, key=lambda q: q["rate_usd"])


def handler(event: dict, context: Any) -> dict:
    """Assign the lowest-rate carrier to a shipment.

    Args:
        event: Input payload with shipment_id, origin, destination, weight_kg.
               Optional: idempotency_key for safe Step Functions retries.
        context: Lambda context object.

    Returns:
        Dict with carrier_id, carrier_name, rate_usd, service_type.
    """
    logger.info("AssignCarrier invoked: %s", json.dumps(event, default=str))

    # Check idempotency — return cached result if this is a retry
    idempotency_key = event.get("idempotency_key")
    if idempotency_key:
        cached = _check_idempotency(idempotency_key)
        if cached:
            return cached

    # Validate required fields
    required_fields = ["shipment_id", "origin", "destination", "weight_kg"]
    missing = [f for f in required_fields if f not in event or event[f] is None]
    if missing:
        error_msg = f"Missing required fields: {', '.join(missing)}"
        logger.error(error_msg)
        return {"statusCode": 400, "error": error_msg}

    # Extract input fields
    shipment_id: str = event["shipment_id"]
    origin: str = event["origin"]
    destination: str = event["destination"]

    try:
        weight_kg: float = float(event["weight_kg"])
    except (TypeError, ValueError):
        error_msg = "weight_kg must be a number"
        logger.error(error_msg)
        return {"statusCode": 400, "error": error_msg}

    if weight_kg <= 0:
        error_msg = "weight_kg must be positive"
        logger.error(error_msg)
        return {"statusCode": 400, "error": error_msg}

    # Invoke carrier simulator to get rate quotes
    try:
        quotes = _invoke_carrier_simulator(origin, destination, weight_kg)
    except RuntimeError as e:
        error_msg = str(e)
        logger.error("Carrier simulation failed: %s", error_msg)
        return {"statusCode": 502, "error": error_msg}

    # Select lowest-rate carrier
    best_quote = _select_lowest_rate(quotes)
    now = datetime.now(timezone.utc).isoformat()

    carrier_id: str = best_quote["carrier_id"]
    carrier_name: str = best_quote["carrier_name"]
    rate_usd: float = best_quote["rate_usd"]
    service_type: str = best_quote["service_type"]

    # Update DynamoDB shipment record with assigned carrier
    update_shipment(shipment_id, {
        "status": "carrier_assigned",
        "carrier_id": carrier_id,
        "carrier_name": carrier_name,
        "rate_usd": str(rate_usd),
        "service_type": service_type,
        "updated_at": now,
    })
    logger.info(
        "Shipment %s assigned to carrier %s (%s) at $%.2f",
        shipment_id,
        carrier_id,
        carrier_name,
        rate_usd,
    )

    # Store the carrier assignment event in DynamoDB
    event_data = {
        "event_type": "CarrierAssigned",
        "shipment_id": shipment_id,
        "payload": json.dumps({
            "carrier_id": carrier_id,
            "carrier_name": carrier_name,
            "rate_usd": rate_usd,
            "service_type": service_type,
            "quotes_evaluated": len(quotes),
        }),
    }
    put_shipment_event(shipment_id, now, event_data)

    # Publish CarrierAssigned event to EventBridge (standardized envelope)
    publish_event(
        event_type="CarrierAssigned",
        shipment_id=shipment_id,
        step="AssignCarrier",
        timestamp=now,
        payload={
            "carrier_id": carrier_id,
            "carrier_name": carrier_name,
            "rate_usd": rate_usd,
            "service_type": service_type,
            "quotes_evaluated": len(quotes),
        },
    )

    # Build response
    response = {
        "carrier_id": carrier_id,
        "carrier_name": carrier_name,
        "rate_usd": rate_usd,
        "service_type": service_type,
    }

    # Store idempotency record for safe retries
    if idempotency_key:
        _store_idempotency(idempotency_key, response)

    logger.info("AssignCarrier response: %s", json.dumps(response, default=str))
    return response
