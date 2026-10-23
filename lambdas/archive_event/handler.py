"""Event Archiver Lambda — writes every TMS domain event to S3 for Athena.

Triggered by an EventBridge rule that matches all ``tms.mvp`` events. Each
event is written as a single JSON object to the archive bucket, partitioned by
event date so Athena/Glue can query shipment history and SLM-vs-LLM usage.

Object key layout (Hive-style partitions for Athena)::

    events/dt=YYYY-MM-DD/<event_type>/<shipment_id>-<timestamp>.json

The stored record is the flattened standardized event detail (see
``shared/events.py``): event_type, shipment_id, timestamp, step, model_tier,
confidence, and the event-specific payload.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ARCHIVE_BUCKET = os.environ.get("ARCHIVE_BUCKET", "")

_s3 = None


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def handler(event: dict, context: Any) -> dict:
    """Archive a single EventBridge event to S3.

    Args:
        event: The EventBridge event envelope. ``event["detail"]`` holds the
            standardized TMS event detail; ``event["detail-type"]`` is the event
            type.
        context: Lambda context (unused).

    Returns:
        A small dict with the written S3 key (for logging/testing).
    """
    logger.info("Archiver received event: %s", json.dumps(event, default=str))

    detail = event.get("detail", {}) or {}
    event_type = detail.get("event_type") or event.get("detail-type", "Unknown")
    shipment_id = detail.get("shipment_id", "unknown")
    timestamp = detail.get("timestamp") or datetime.now(timezone.utc).isoformat()

    # Derive a date partition from the event timestamp (fall back to today).
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, AttributeError):
        dt = datetime.now(timezone.utc).date().isoformat()

    # Sanitize timestamp for use in a key.
    safe_ts = timestamp.replace(":", "").replace("+", "_")
    key = f"events/dt={dt}/{event_type}/{shipment_id}-{safe_ts}.json"

    if not ARCHIVE_BUCKET:
        logger.error("ARCHIVE_BUCKET not configured; cannot archive event")
        return {"archived": False, "reason": "ARCHIVE_BUCKET not set"}

    # Store the flattened detail as the record (one JSON object per event).
    record = {
        "event_type": event_type,
        "shipment_id": shipment_id,
        "timestamp": timestamp,
        "step": detail.get("step"),
        "model_tier": detail.get("model_tier"),
        "confidence": detail.get("confidence"),
        "payload": detail.get("payload", {}),
    }

    try:
        _s3_client().put_object(
            Bucket=ARCHIVE_BUCKET,
            Key=key,
            Body=json.dumps(record, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("Archived %s event to s3://%s/%s", event_type, ARCHIVE_BUCKET, key)
        return {"archived": True, "key": key}
    except Exception as e:  # noqa: BLE001 — archival must not raise back to EB
        logger.error("Failed to archive event to S3: %s", str(e))
        return {"archived": False, "reason": str(e)}
