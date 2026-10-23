"""Detect Exception Lambda — tms-detect-exception.

Simulates exception detection during shipment monitoring. For the demo,
this Lambda ALWAYS detects a "delay" exception to trigger the Intelligence
Agent for exception analysis.

This Lambda is invoked directly by Step Functions as part of the deterministic
shipment lifecycle workflow.

Input: shipment_id
Output: exception_detected (bool), exception_type, severity_hint
Side effects: updates DynamoDB status to "exception", publishes ExceptionDetected event
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# Add project root to path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.dynamo_utils import get_shipment, put_shipment_event, update_shipment
from shared.events import publish_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context: Any) -> dict:
    """Detect shipment exceptions (simulated for demo).

    For the MVP demo, this always detects a "delay" exception so the
    Intelligence Agent is triggered to analyze and resolve it.

    Args:
        event: Must contain 'shipment_id'. Passed from Step Functions.
        context: Lambda context object.

    Returns:
        Dict with exception_detected, exception_type, and severity_hint.
    """
    logger.info("DetectException invoked: %s", json.dumps(event))

    shipment_id: str = event.get("shipment_id", "")
    if not shipment_id:
        logger.error("Missing required field: shipment_id")
        return {
            "statusCode": 400,
            "body": {"error": "Missing required field: shipment_id"},
        }

    # Verify shipment exists in DynamoDB
    shipment = get_shipment(shipment_id)
    if not shipment:
        logger.error("Shipment not found: %s", shipment_id)
        return {
            "statusCode": 404,
            "body": {"error": f"Shipment not found: {shipment_id}"},
        }

    # For demo purposes: always detect a "delay" exception
    exception_type = "delay"
    severity_hint = "medium"
    now = datetime.now(timezone.utc).isoformat()

    # Update shipment status to "exception" in DynamoDB
    update_shipment(
        shipment_id,
        {
            "status": "exception",
            "exception_type": exception_type,
            "severity_hint": severity_hint,
            "exception_detected_at": now,
            "updated_at": now,
        },
    )
    logger.info(
        "Shipment %s status updated to exception: type=%s, severity=%s",
        shipment_id,
        exception_type,
        severity_hint,
    )

    # Store the exception event in DynamoDB
    event_data = {
        "event_type": "ExceptionDetected",
        "shipment_id": shipment_id,
        "payload": json.dumps({
            "exception_type": exception_type,
            "severity_hint": severity_hint,
        }),
    }
    put_shipment_event(shipment_id, now, event_data)

    # Publish ExceptionDetected event to EventBridge (standardized envelope)
    publish_event(
        event_type="ExceptionDetected",
        shipment_id=shipment_id,
        step="DetectException",
        timestamp=now,
        payload={
            "exception_type": exception_type,
            "severity_hint": severity_hint,
        },
    )

    response = {
        "shipment_id": shipment_id,
        "exception_detected": True,
        "exception_type": exception_type,
        "severity_hint": severity_hint,
    }

    logger.info("DetectException response: %s", json.dumps(response))
    return response
