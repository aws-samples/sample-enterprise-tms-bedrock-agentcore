"""Telematics Processor Lambda — consumes the Kinesis tracking stream.

Per the blog's Tier 1 (Execution monitoring, lifecycle step 4), real-time
tracking updates flow through Amazon Kinesis. This Lambda is the deterministic
(zero-AI-cost) processor: it reads telematics records off the stream, updates
the shipment's tracking state in DynamoDB, and publishes progress events.

Each Kinesis record is expected to carry a JSON telematics update::

    {"shipment_id": "...", "status": "in_transit", "location": "...",
     "eta": "...", "event": "location_update" | "delay" | "damage"}

This handler is intentionally deterministic — no model invocation.
"""

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any

from shared.dynamo_utils import get_shipment, update_shipment

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event: dict, context: Any) -> dict:
    """Process a batch of Kinesis telematics records.

    Args:
        event: Kinesis event with ``Records``; each record's ``kinesis.data`` is
            base64-encoded JSON.
        context: Lambda context (unused).

    Returns:
        Summary dict with processed/failed counts.
    """
    records = event.get("Records", [])
    logger.info("Telematics processor received %d record(s)", len(records))

    processed = 0
    failed = 0

    for record in records:
        try:
            raw = record["kinesis"]["data"]
            decoded = base64.b64decode(raw).decode("utf-8")
            update = json.loads(decoded)
            _apply_tracking_update(update)
            processed += 1
        except Exception as e:  # noqa: BLE001 — skip bad records, keep the batch moving
            logger.error("Failed to process telematics record: %s", str(e))
            failed += 1

    logger.info("Telematics batch complete: processed=%d failed=%d", processed, failed)
    return {"processed": processed, "failed": failed}


def _apply_tracking_update(update: dict) -> None:
    """Apply a single telematics update to the shipment's DynamoDB record."""
    shipment_id = update.get("shipment_id")
    if not shipment_id:
        logger.warning("Telematics update missing shipment_id; skipping")
        return

    shipment = get_shipment(shipment_id)
    if not shipment:
        logger.warning("Telematics update for unknown shipment %s; skipping", shipment_id)
        return

    now = datetime.now(timezone.utc).isoformat()
    updates = {
        "tracking_status": update.get("status", shipment.get("status", "in_transit")),
        "current_location": update.get("location", ""),
        "eta": update.get("eta", ""),
        "updated_at": now,
    }
    update_shipment(shipment_id, updates)
    logger.info(
        "Applied telematics update to %s: status=%s location=%s",
        shipment_id,
        updates["tracking_status"],
        updates["current_location"],
    )
