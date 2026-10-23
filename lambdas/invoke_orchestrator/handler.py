"""Invoke Orchestrator Lambda — HTTP ingress behind API Gateway.

Wraps ``agents.orchestrator_agent.invoke_orchestrator_agent``: it classifies an
incoming shipment request (SLM-first, LLM fallback) and starts the Step
Functions lifecycle workflow. Fronted by API Gateway (POST /shipments) with
Cognito authorization.

Request body (JSON): {origin, destination, weight_kg, commodity, delivery_date}
Response (JSON): {classification, confidence, model_tier, execution_arn}
"""

import json
import logging
import os
import sys
from typing import Any

# Ensure project root is importable (agents/, shared/).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agents.orchestrator_agent import invoke_orchestrator_agent

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "OPTIONS,POST",
}

_REQUIRED = ["origin", "destination", "weight_kg", "commodity", "delivery_date"]


def _response(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": _CORS_HEADERS, "body": json.dumps(body, default=str)}


def handler(event: dict, context: Any) -> dict:
    """API Gateway proxy handler for POST /shipments."""
    logger.info("Orchestrator ingress event: %s", json.dumps(event, default=str)[:2000])

    # API Gateway proxy passes the JSON body as a string.
    raw_body = event.get("body")
    if raw_body is None:
        # Allow direct (non-proxy) invocation with the payload as the event.
        payload = {k: event.get(k) for k in _REQUIRED if k in event}
    else:
        try:
            payload = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
        except (json.JSONDecodeError, TypeError):
            return _response(400, {"error": "Request body must be valid JSON"})

    missing = [f for f in _REQUIRED if f not in payload or payload[f] in (None, "")]
    if missing:
        return _response(400, {"error": f"Missing required fields: {', '.join(missing)}"})

    try:
        result = invoke_orchestrator_agent(payload)
    except Exception as e:  # noqa: BLE001
        logger.error("Orchestrator invocation failed: %s", str(e))
        return _response(502, {"error": f"Orchestrator failed: {e}"})

    if result.get("error") and not result.get("execution_arn"):
        return _response(502, result)

    return _response(200, result)
