"""Invoke Intelligence Agent Lambda — Step Functions agent handoff wrapper.

This Lambda is invoked by Step Functions as part of the shipment lifecycle workflow.
It wraps the Intelligence Agent invocation, constructing the agent input payload,
calling the agent, and returning the structured exception analysis result to Step Functions.

After a successful agent invocation, it publishes an ExceptionResolved event to EventBridge.

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

from agents.intelligence_agent import invoke_intelligence_agent
from shared.events import publish_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context: Any) -> dict:
    """Step Functions invokes this Lambda to hand off to the Intelligence Agent.

    The wrapper:
    1. Receives shipment_id, exception_type, severity_hint from Step Functions
    2. Constructs the Intelligence Agent input payload
    3. Invokes the Intelligence Agent (SLM-first, LLM fallback)
    4. Returns structured exception analysis result to Step Functions
    5. Publishes ExceptionResolved event to EventBridge

    Args:
        event: Dict containing shipment_id, exception_type, severity_hint.
        context: Lambda context object.

    Returns:
        Dict with exception_type, severity, resolution_recommendation,
        confidence, and model_tier for Step Functions.
    """
    logger.info("InvokeIntelligenceAgent invoked: %s", json.dumps(event))

    # Validate required fields
    required_fields = ["shipment_id", "exception_type", "severity_hint"]
    missing = [f for f in required_fields if not event.get(f)]
    if missing:
        logger.error("Missing required fields: %s", missing)
        return {
            "statusCode": 400,
            "body": {"error": f"Missing required fields: {missing}"},
        }

    shipment_id: str = event["shipment_id"]
    exception_type: str = event["exception_type"]
    severity_hint: str = event["severity_hint"]

    # Construct agent input payload
    agent_payload = {
        "shipment_id": shipment_id,
        "exception_type": exception_type,
        "severity_hint": severity_hint,
    }

    logger.info(
        "Invoking Intelligence Agent for shipment %s: exception_type=%s, severity_hint=%s",
        shipment_id,
        exception_type,
        severity_hint,
    )

    # Invoke the Intelligence Agent
    try:
        agent_result = invoke_intelligence_agent(agent_payload)
    except Exception as e:
        logger.error("Intelligence Agent invocation failed: %s", str(e))
        return {
            "statusCode": 500,
            "body": {"error": f"Intelligence Agent invocation failed: {str(e)}"},
        }

    # Build structured response for Step Functions
    now = datetime.now(timezone.utc).isoformat()
    response = {
        "shipment_id": shipment_id,
        "exception_type": agent_result.get("exception_type", exception_type),
        "severity": agent_result.get("severity", severity_hint),
        "resolution_recommendation": agent_result.get("resolution_recommendation", ""),
        "confidence": agent_result.get("confidence", 0.0),
        "model_tier": agent_result.get("model_tier", "SLM"),
    }

    logger.info(
        "Intelligence Agent result: model_tier=%s, confidence=%.2f, severity=%s",
        response["model_tier"],
        response["confidence"],
        response["severity"],
    )

    # Publish ExceptionResolved event to EventBridge (standardized envelope)
    publish_event(
        event_type="ExceptionResolved",
        shipment_id=shipment_id,
        step="AnalyzeException",
        timestamp=now,
        payload={
            "exception_type": response["exception_type"],
            "severity": response["severity"],
            "resolution_recommendation": response["resolution_recommendation"],
        },
        model_tier=response["model_tier"],
        confidence=response["confidence"],
    )

    logger.info("InvokeIntelligenceAgent response: %s", json.dumps(response))
    return response
