"""Shared domain-event publishing for the TMS system.

This module is the single source of truth for the EventBridge event envelope.
All lifecycle Lambdas publish through :func:`publish_event` so that the schema
stays consistent across every producer and every consumer — the SQS progress
queue, the S3 archive, and the AppSync real-time channel all receive the same
shape.

Standardized detail envelope (EventBridge ``Detail``)::

    {
        "event_type": "RouteOptimized",     # matches the EventBridge DetailType
        "shipment_id": "ship-abc123",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "step": "OptimizeRoute",
        "model_tier": "SLM" | "LLM" | None,  # top-level for AI steps, None otherwise
        "confidence": 0.87 | None,            # top-level for AI steps, None otherwise
        "payload": { ... event-specific fields ... }
    }

Design notes:
- ``event_type`` is carried BOTH as the EventBridge ``DetailType`` and as a
  top-level field inside ``Detail`` so consumers that only see the detail body
  (e.g. AppSync, S3 records) don't have to reach for the envelope's detail-type.
- ``model_tier`` and ``confidence`` are promoted to the TOP LEVEL of the detail
  (previously they were nested inside ``payload`` for AI steps, which the
  progress-queue consumer failed to read). They are ``None`` for deterministic steps.
- Publishing is non-blocking: failures are logged, never raised, so a telemetry
  problem can't fail a lifecycle Lambda.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import boto3

from shared.constants import EVENT_BUS_NAME, EVENT_SOURCE, EVENT_TYPES

logger = logging.getLogger(__name__)

# Reuse a single EventBridge client across warm invocations.
_events_client = None


def _client():
    global _events_client
    if _events_client is None:
        _events_client = boto3.client("events")
    return _events_client


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def build_detail(
    event_type: str,
    shipment_id: str,
    step: str,
    payload: dict,
    timestamp: Optional[str] = None,
    model_tier: Optional[str] = None,
    confidence: Optional[float] = None,
) -> dict:
    """Build the standardized event detail body.

    Exposed separately from :func:`publish_event` so the same envelope can be
    reused when writing to non-EventBridge sinks (e.g. seeding a test fixture
    or archiving directly to S3).
    """
    return {
        "event_type": event_type,
        "shipment_id": shipment_id,
        "timestamp": timestamp or utc_now_iso(),
        "step": step,
        "model_tier": model_tier,
        "confidence": confidence,
        "payload": payload,
    }


def publish_event(
    event_type: str,
    shipment_id: str,
    step: str,
    payload: dict,
    timestamp: Optional[str] = None,
    model_tier: Optional[str] = None,
    confidence: Optional[float] = None,
    bus_name: Optional[str] = None,
) -> None:
    """Publish a domain event to EventBridge using the standardized envelope.

    Args:
        event_type: One of :data:`shared.constants.EVENT_TYPES`. Used as both
            the EventBridge ``DetailType`` and a top-level ``event_type`` field.
        shipment_id: The shipment this event relates to.
        step: The lifecycle/workflow step that produced the event.
        payload: Event-specific fields (a JSON-serializable dict).
        timestamp: ISO-8601 timestamp; defaults to now (UTC).
        model_tier: "SLM" or "LLM" for AI steps; ``None`` for deterministic steps.
        confidence: Agent confidence (0.0-1.0) for AI steps; ``None`` otherwise.
        bus_name: Override the target bus; defaults to ``EVENT_BUS_NAME`` env/const.

    This function never raises — publishing telemetry must not fail the caller.
    """
    if event_type not in EVENT_TYPES:
        # Not fatal, but worth surfacing: a typo here means consumers filtering
        # on known types would silently drop the event.
        logger.warning("Publishing unknown event_type '%s' (not in EVENT_TYPES)", event_type)

    target_bus = bus_name or os.environ.get("EVENT_BUS_NAME", EVENT_BUS_NAME)
    detail = build_detail(
        event_type=event_type,
        shipment_id=shipment_id,
        step=step,
        payload=payload,
        timestamp=timestamp,
        model_tier=model_tier,
        confidence=confidence,
    )

    try:
        response = _client().put_events(
            Entries=[
                {
                    "Source": EVENT_SOURCE,
                    "DetailType": event_type,
                    "Detail": json.dumps(detail),
                    "EventBusName": target_bus,
                }
            ]
        )
        failed = response.get("FailedEntryCount", 0)
        if failed:
            logger.warning(
                "EventBridge publish had %d failed entries for %s: %s",
                failed,
                event_type,
                json.dumps(response.get("Entries", []), default=str),
            )
        else:
            logger.info("%s event published for shipment_id=%s", event_type, shipment_id)
    except Exception as e:  # noqa: BLE001 — telemetry must never fail the caller
        logger.error("Failed to publish %s event: %s", event_type, str(e))
