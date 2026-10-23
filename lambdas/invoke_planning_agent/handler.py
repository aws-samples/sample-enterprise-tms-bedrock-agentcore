"""Invoke Planning Agent Lambda — Step Functions agent handoff wrapper.

This Lambda is invoked by Step Functions as part of the shipment lifecycle workflow.
It wraps the Planning Agent invocation, constructing the agent input payload,
calling the agent, and returning the structured route result to Step Functions.

After a successful agent invocation, it publishes a RouteOptimized event to EventBridge.

Requirements: 3.3, 3.4
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# Add project root to path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agents.planning_agent import invoke_planning_agent
from shared.events import publish_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context: Any) -> dict:
    """Step Functions invokes this Lambda to hand off to the Planning Agent.

    The wrapper:
    1. Receives shipment_id, origin, destination, weight_kg from Step Functions
    2. Constructs the Planning Agent input payload
    3. Invokes the Planning Agent (SLM-first, LLM fallback)
    4. Returns structured route result to Step Functions
    5. Publishes RouteOptimized event to EventBridge

    Args:
        event: Dict containing shipment_id, origin, destination, weight_kg.
        context: Lambda context object.

    Returns:
        Dict with route, confidence, and model_tier for Step Functions.
    """
    logger.info("InvokePlanningAgent invoked: %s", json.dumps(event))

    # Validate required fields
    required_fields = ["shipment_id", "origin", "destination", "weight_kg"]
    missing = [f for f in required_fields if not event.get(f)]
    if missing:
        logger.error("Missing required fields: %s", missing)
        return {
            "statusCode": 400,
            "body": {"error": f"Missing required fields: {missing}"},
        }

    shipment_id: str = event["shipment_id"]
    origin: str = event["origin"]
    destination: str = event["destination"]
    weight_kg = event["weight_kg"]

    # Construct agent input payload
    agent_payload = {
        "origin": origin,
        "destination": destination,
        "weight_kg": weight_kg,
        "shipment_id": shipment_id,
    }

    logger.info(
        "Invoking Planning Agent for shipment %s: %s → %s (%.1f kg)",
        shipment_id,
        origin,
        destination,
        float(weight_kg),
    )

    # Invoke the Planning Agent
    try:
        agent_result = invoke_planning_agent(agent_payload)
    except Exception as e:
        logger.error("Planning Agent invocation failed: %s", str(e))
        return {
            "statusCode": 500,
            "body": {"error": f"Planning Agent invocation failed: {str(e)}"},
        }

    # Build structured response for Step Functions
    now = datetime.now(timezone.utc).isoformat()
    response = {
        "shipment_id": shipment_id,
        "route": agent_result.get("route", {}),
        "confidence": agent_result.get("confidence", 0.0),
        "model_tier": agent_result.get("model_tier", "SLM"),
    }

    logger.info(
        "Planning Agent result: model_tier=%s, confidence=%.2f",
        response["model_tier"],
        response["confidence"],
    )

    # Publish RouteOptimized event to EventBridge (standardized envelope)
    publish_event(
        event_type="RouteOptimized",
        shipment_id=shipment_id,
        step="OptimizeRoute",
        timestamp=now,
        payload={"route": response["route"]},
        model_tier=response["model_tier"],
        confidence=response["confidence"],
    )

    logger.info("InvokePlanningAgent response: %s", json.dumps(response))
    return response
