"""Complete Shipment Lambda — tms-complete-shipment.

Transitions a shipment to "complete" status in DynamoDB and publishes a
ShipmentCompleted event to EventBridge. This is the final step of the
shipment lifecycle workflow.

Input: shipment_id
Output: shipment_id, final_status ("complete"), completed_at
Side effects: updates DynamoDB status, publishes ShipmentCompleted event

Validates: Requirements 8.5, 10.6
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import boto3

# Add project root to path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.dynamo_utils import get_shipment, put_shipment_event, update_shipment
from shared.events import publish_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context: Any) -> dict:
    """Transition shipment to complete status.

    Args:
        event: Must contain 'shipment_id'. Passed from Step Functions.
        context: Lambda context object.

    Returns:
        Dict with shipment_id, final_status ("complete"), and completed_at.
    """
    logger.info("CompleteShipment invoked: %s", json.dumps(event))

    shipment_id: str = event.get("shipment_id", "")
    if not shipment_id:
        logger.error("Missing required field: shipment_id")
        return {
            "statusCode": 400,
            "body": {"error": "Missing required field: shipment_id"},
        }

    # Fetch existing shipment to verify it exists
    shipment = get_shipment(shipment_id)
    if not shipment:
        logger.error("Shipment not found: %s", shipment_id)
        return {
            "statusCode": 404,
            "body": {"error": f"Shipment not found: {shipment_id}"},
        }

    # Transition to complete status
    now = datetime.now(timezone.utc).isoformat()
    update_shipment(
        shipment_id,
        {
            "status": "complete",
            "completed_at": now,
            "updated_at": now,
        },
    )
    logger.info("Shipment %s marked as complete at %s", shipment_id, now)

    # Store completion event in DynamoDB
    event_data = {
        "event_type": "ShipmentCompleted",
        "shipment_id": shipment_id,
        "payload": json.dumps({"final_status": "complete", "completed_at": now}),
    }
    put_shipment_event(shipment_id, now, event_data)

    # Publish ShipmentCompleted event to EventBridge (standardized envelope)
    publish_event(
        event_type="ShipmentCompleted",
        shipment_id=shipment_id,
        step="CompleteShipment",
        timestamp=now,
        payload={
            "final_status": "complete",
            "completed_at": now,
        },
    )

    # Build response
    response = {
        "shipment_id": shipment_id,
        "final_status": "complete",
        "completed_at": now,
    }

    logger.info("CompleteShipment response: %s", json.dumps(response))
    return response



