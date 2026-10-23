"""Seed Data Custom Resource Lambda.

Populates the TMS MVP DynamoDB table with:
- 5 carriers (UPS, FedEx, XPO Logistics, J.B. Hunt, Schneider)
- 10-15 rates for 5 lanes × 2-3 carriers each
- 1 sample shipment for reference

This Lambda is invoked as a CloudFormation Custom Resource during `cdk deploy`.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("TABLE_NAME", "tms-mvp-table")


def handler(event: dict, context: Any) -> dict:
    """CloudFormation Custom Resource handler for seed data population.

    Handles Create, Update, and Delete events from CloudFormation.
    On Create/Update: populates the DynamoDB table with seed data.
    On Delete: no-op (table deletion handles cleanup).
    """
    logger.info("Received event: %s", json.dumps(event))

    request_type = event.get("RequestType", "Create")
    response_url = event.get("ResponseURL", "")
    stack_id = event.get("StackId", "")
    request_id = event.get("RequestId", "")
    logical_resource_id = event.get("LogicalResourceId", "")
    physical_resource_id = event.get("PhysicalResourceId", "seed-data-resource")

    try:
        if request_type in ("Create", "Update"):
            _populate_seed_data()
            logger.info("Seed data populated successfully.")

        _send_cfn_response(
            response_url=response_url,
            stack_id=stack_id,
            request_id=request_id,
            logical_resource_id=logical_resource_id,
            physical_resource_id=physical_resource_id,
            status="SUCCESS",
            reason="Seed data loaded successfully.",
        )
    except Exception as e:
        logger.error("Error populating seed data: %s", str(e))
        _send_cfn_response(
            response_url=response_url,
            stack_id=stack_id,
            request_id=request_id,
            logical_resource_id=logical_resource_id,
            physical_resource_id=physical_resource_id,
            status="FAILED",
            reason=str(e),
        )

    return {"StatusCode": 200}


def _populate_seed_data() -> None:
    """Write all seed data items to DynamoDB using batch_write_item."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(TABLE_NAME)

    items = _build_carrier_items() + _build_rate_items() + _build_sample_shipment()

    # Use batch writer for efficient writes
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)

    logger.info("Wrote %d seed data items to table %s", len(items), TABLE_NAME)


def _build_carrier_items() -> list[dict]:
    """Build 5 carrier profile records."""
    carriers = [
        {
            "carrier_id": "carrier-ups",
            "name": "UPS",
            "service_type": "parcel_and_freight",
            "coverage_region": "nationwide",
            "rating": 4.5,
        },
        {
            "carrier_id": "carrier-fedex",
            "name": "FedEx",
            "service_type": "express_and_freight",
            "coverage_region": "nationwide",
            "rating": 4.4,
        },
        {
            "carrier_id": "carrier-xpo",
            "name": "XPO Logistics",
            "service_type": "ltl_freight",
            "coverage_region": "nationwide",
            "rating": 4.2,
        },
        {
            "carrier_id": "carrier-jbhunt",
            "name": "J.B. Hunt",
            "service_type": "intermodal_and_truckload",
            "coverage_region": "nationwide",
            "rating": 4.3,
        },
        {
            "carrier_id": "carrier-schneider",
            "name": "Schneider",
            "service_type": "truckload_and_ltl",
            "coverage_region": "nationwide",
            "rating": 4.1,
        },
    ]

    items = []
    for c in carriers:
        items.append(
            {
                "PK": f"CARRIER#{c['carrier_id']}",
                "SK": "PROFILE",
                "name": c["name"],
                "service_type": c["service_type"],
                "coverage_region": c["coverage_region"],
                "rating": str(c["rating"]),
            }
        )
    return items


def _build_rate_items() -> list[dict]:
    """Build 13 rate records for 5 lanes × 2-3 carriers each."""
    # 5 lanes: Chicago→NYC, LA→Dallas, Seattle→Denver, Atlanta→Miami, Boston→Philadelphia
    rates = [
        # Lane 1: Chicago, IL → New York, NY
        {
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "carrier_id": "carrier-ups",
            "rate_usd": "1245.00",
            "transit_days": 3,
            "weight_tier": "500-1000kg",
            "service_type": "ground_freight",
        },
        {
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "carrier_id": "carrier-fedex",
            "rate_usd": "1320.00",
            "transit_days": 2,
            "weight_tier": "500-1000kg",
            "service_type": "express_freight",
        },
        {
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "carrier_id": "carrier-xpo",
            "rate_usd": "1100.00",
            "transit_days": 4,
            "weight_tier": "500-1000kg",
            "service_type": "ltl_freight",
        },
        # Lane 2: Los Angeles, CA → Dallas, TX
        {
            "origin": "Los Angeles, CA",
            "destination": "Dallas, TX",
            "carrier_id": "carrier-jbhunt",
            "rate_usd": "1580.00",
            "transit_days": 3,
            "weight_tier": "1000-2000kg",
            "service_type": "truckload",
        },
        {
            "origin": "Los Angeles, CA",
            "destination": "Dallas, TX",
            "carrier_id": "carrier-schneider",
            "rate_usd": "1490.00",
            "transit_days": 3,
            "weight_tier": "1000-2000kg",
            "service_type": "truckload",
        },
        {
            "origin": "Los Angeles, CA",
            "destination": "Dallas, TX",
            "carrier_id": "carrier-ups",
            "rate_usd": "1650.00",
            "transit_days": 2,
            "weight_tier": "1000-2000kg",
            "service_type": "express_freight",
        },
        # Lane 3: Seattle, WA → Denver, CO
        {
            "origin": "Seattle, WA",
            "destination": "Denver, CO",
            "carrier_id": "carrier-fedex",
            "rate_usd": "980.00",
            "transit_days": 2,
            "weight_tier": "200-500kg",
            "service_type": "express_freight",
        },
        {
            "origin": "Seattle, WA",
            "destination": "Denver, CO",
            "carrier_id": "carrier-xpo",
            "rate_usd": "850.00",
            "transit_days": 3,
            "weight_tier": "200-500kg",
            "service_type": "ltl_freight",
        },
        # Lane 4: Atlanta, GA → Miami, FL
        {
            "origin": "Atlanta, GA",
            "destination": "Miami, FL",
            "carrier_id": "carrier-ups",
            "rate_usd": "720.00",
            "transit_days": 2,
            "weight_tier": "100-200kg",
            "service_type": "ground_freight",
        },
        {
            "origin": "Atlanta, GA",
            "destination": "Miami, FL",
            "carrier_id": "carrier-jbhunt",
            "rate_usd": "680.00",
            "transit_days": 2,
            "weight_tier": "100-200kg",
            "service_type": "intermodal",
        },
        {
            "origin": "Atlanta, GA",
            "destination": "Miami, FL",
            "carrier_id": "carrier-schneider",
            "rate_usd": "750.00",
            "transit_days": 1,
            "weight_tier": "100-200kg",
            "service_type": "truckload",
        },
        # Lane 5: Boston, MA → Philadelphia, PA
        {
            "origin": "Boston, MA",
            "destination": "Philadelphia, PA",
            "carrier_id": "carrier-fedex",
            "rate_usd": "550.00",
            "transit_days": 1,
            "weight_tier": "50-100kg",
            "service_type": "express_freight",
        },
        {
            "origin": "Boston, MA",
            "destination": "Philadelphia, PA",
            "carrier_id": "carrier-xpo",
            "rate_usd": "480.00",
            "transit_days": 2,
            "weight_tier": "50-100kg",
            "service_type": "ltl_freight",
        },
    ]

    items = []
    for r in rates:
        items.append(
            {
                "PK": f"RATE#{r['origin']}#{r['destination']}",
                "SK": f"CARRIER#{r['carrier_id']}",
                "rate_usd": r["rate_usd"],
                "transit_days": r["transit_days"],
                "weight_tier": r["weight_tier"],
                "service_type": r["service_type"],
            }
        )
    return items


def _build_sample_shipment() -> list[dict]:
    """Build 1 sample shipment record for reference."""
    now = datetime.now(timezone.utc).isoformat()
    shipment_id = "sample-shipment-001"

    return [
        {
            "PK": f"SHIPMENT#{shipment_id}",
            "SK": "STATUS",
            "shipment_id": shipment_id,
            "status": "created",
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": "750",
            "commodity": "Electronics",
            "delivery_date": "2025-02-15",
            "created_at": now,
            "updated_at": now,
        }
    ]


def _send_cfn_response(
    response_url: str,
    stack_id: str,
    request_id: str,
    logical_resource_id: str,
    physical_resource_id: str,
    status: str,
    reason: str,
) -> None:
    """Send response back to CloudFormation."""
    if not response_url:
        logger.info("No ResponseURL — skipping CFN response (local test mode).")
        return

    response_body = json.dumps(
        {
            "Status": status,
            "Reason": reason,
            "PhysicalResourceId": physical_resource_id,
            "StackId": stack_id,
            "RequestId": request_id,
            "LogicalResourceId": logical_resource_id,
            "Data": {},
        }
    )

    # response_url is the pre-signed S3 URL supplied by CloudFormation for the
    # Custom Resource callback; it is always HTTPS. Guard the scheme regardless.
    if not response_url.lower().startswith("https://"):
        logger.error("CFN ResponseURL is not HTTPS; skipping callback")
        return

    # Use urllib3 (bundled with botocore) for the HTTPS PUT callback.
    http = urllib3.PoolManager()
    response = http.request(
        "PUT",
        response_url,
        body=response_body.encode("utf-8"),
        headers={"Content-Type": ""},
    )
    logger.info("CFN response status: %s", response.status)
