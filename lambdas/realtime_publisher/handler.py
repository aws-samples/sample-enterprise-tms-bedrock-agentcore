"""Real-time Publisher Lambda — EventBridge → AppSync mutation.

Invoked by an EventBridge rule matching all ``tms.mvp`` events. For each event
it calls the AppSync ``publishShipmentEvent`` mutation (authorized with IAM /
SigV4), which fans the event out to any browser subscribed via
``onShipmentEvent``. This is what drives the Next.js cockpit's live feed.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

logger = logging.getLogger()
logger.setLevel(logging.INFO)

APPSYNC_API_URL = os.environ.get("APPSYNC_API_URL", "")
APPSYNC_REGION = os.environ.get("APPSYNC_REGION", os.environ.get("AWS_REGION", "us-east-1"))

_MUTATION = """
mutation Publish(
  $event_type: String!, $shipment_id: String!, $timestamp: String!,
  $step: String, $model_tier: String, $confidence: Float, $payload: AWSJSON
) {
  publishShipmentEvent(
    event_type: $event_type, shipment_id: $shipment_id, timestamp: $timestamp,
    step: $step, model_tier: $model_tier, confidence: $confidence, payload: $payload
  ) { event_type shipment_id timestamp }
}
"""


def handler(event: dict, context: Any) -> dict:
    """Publish a single EventBridge event to AppSync via a SigV4-signed mutation."""
    detail = event.get("detail", {}) or {}
    variables = {
        "event_type": detail.get("event_type") or event.get("detail-type", "Unknown"),
        "shipment_id": detail.get("shipment_id", "unknown"),
        "timestamp": detail.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "step": detail.get("step"),
        "model_tier": detail.get("model_tier"),
        "confidence": detail.get("confidence"),
        # AWSJSON expects a JSON string.
        "payload": json.dumps(detail.get("payload", {}), default=str),
    }

    if not APPSYNC_API_URL:
        logger.error("APPSYNC_API_URL not configured; cannot publish")
        return {"published": False, "reason": "APPSYNC_API_URL not set"}

    body = json.dumps({"query": _MUTATION, "variables": variables}).encode("utf-8")

    # SigV4-sign the request against the 'appsync' service.
    session = boto3.Session()
    credentials = session.get_credentials()
    aws_request = AWSRequest(
        method="POST",
        url=APPSYNC_API_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(credentials, "appsync", APPSYNC_REGION).add_auth(aws_request)

    # The AppSync endpoint comes from a CDK-managed env var and is always HTTPS;
    # guard against any non-HTTPS scheme before sending.
    if not APPSYNC_API_URL.lower().startswith("https://"):
        logger.error("Refusing to publish to non-HTTPS AppSync URL")
        return {"published": False, "reason": "AppSync URL must be HTTPS"}

    # Use urllib3 (bundled with botocore) for the HTTPS POST.
    http = urllib3.PoolManager()
    try:
        resp = http.request(
            "POST",
            APPSYNC_API_URL,
            body=body,
            headers=dict(aws_request.headers),
        )
        resp_body = resp.data.decode("utf-8")
        logger.info("AppSync publish %s -> %s", variables["event_type"], resp.status)
        payload = json.loads(resp_body)
        if payload.get("errors"):
            logger.warning("AppSync returned errors: %s", payload["errors"])
            return {"published": False, "errors": payload["errors"]}
        return {"published": True}
    except Exception as e:  # noqa: BLE001 — real-time push must not fail the pipeline
        logger.error("Failed to publish to AppSync: %s", str(e))
        return {"published": False, "reason": str(e)}
