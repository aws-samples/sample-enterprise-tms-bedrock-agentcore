"""DynamoDB utility helpers for the TMS MVP single-table design.

Provides get, put, and update operations using the PK/SK key patterns
defined in the architecture:
  - CARRIER#{id} / PROFILE
  - RATE#{origin}#{dest} / CARRIER#{id}
  - SHIPMENT#{id} / STATUS
  - SHIPMENT#{id} / EVENT#{timestamp}
"""

import os
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key

from shared.constants import (
    TABLE_NAME,
    PK_CARRIER,
    PK_RATE,
    PK_SHIPMENT,
    SK_PROFILE,
    SK_STATUS,
    SK_EVENT_PREFIX,
)


def _get_table():
    """Return the DynamoDB Table resource."""
    table_name = os.environ.get("TABLE_NAME", TABLE_NAME)
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(table_name)


# --- Generic helpers ---


def get_item(pk: str, sk: str) -> Optional[dict]:
    """Get a single item by PK and SK.

    Returns the item dict or None if not found.
    """
    table = _get_table()
    response = table.get_item(Key={"PK": pk, "SK": sk})
    return response.get("Item")


def put_item(item: dict) -> dict:
    """Put a single item into the table.

    The item must include PK and SK keys.
    Returns the DynamoDB response.
    """
    table = _get_table()
    return table.put_item(Item=item)


def update_item(pk: str, sk: str, updates: dict[str, Any]) -> dict:
    """Update specific attributes on an existing item.

    Args:
        pk: Partition key value.
        sk: Sort key value.
        updates: Dict of attribute names to new values.

    Returns:
        The updated item attributes.
    """
    table = _get_table()

    update_parts = []
    expression_names = {}
    expression_values = {}

    for i, (attr_name, attr_value) in enumerate(updates.items()):
        placeholder_name = f"#attr{i}"
        placeholder_value = f":val{i}"
        update_parts.append(f"{placeholder_name} = {placeholder_value}")
        expression_names[placeholder_name] = attr_name
        expression_values[placeholder_value] = attr_value

    update_expression = "SET " + ", ".join(update_parts)

    response = table.update_item(
        Key={"PK": pk, "SK": sk},
        UpdateExpression=update_expression,
        ExpressionAttributeNames=expression_names,
        ExpressionAttributeValues=expression_values,
        ReturnValues="ALL_NEW",
    )
    return response.get("Attributes", {})


def query_items(pk: str, sk_prefix: Optional[str] = None) -> list[dict]:
    """Query items by PK, optionally filtering by SK prefix.

    Args:
        pk: Partition key value.
        sk_prefix: Optional sort key prefix to filter results.

    Returns:
        List of matching items.
    """
    table = _get_table()

    key_condition = Key("PK").eq(pk)
    if sk_prefix:
        key_condition = key_condition & Key("SK").begins_with(sk_prefix)

    response = table.query(KeyConditionExpression=key_condition)
    return response.get("Items", [])


# --- Domain-specific helpers ---


def get_carrier(carrier_id: str) -> Optional[dict]:
    """Get a carrier profile by ID."""
    return get_item(f"{PK_CARRIER}{carrier_id}", SK_PROFILE)


def get_rates(origin: str, destination: str) -> list[dict]:
    """Get all carrier rates for an origin-destination pair."""
    pk = f"{PK_RATE}{origin}#{destination}"
    return query_items(pk)


def get_shipment(shipment_id: str) -> Optional[dict]:
    """Get shipment status record."""
    return get_item(f"{PK_SHIPMENT}{shipment_id}", SK_STATUS)


def put_shipment(shipment_id: str, attributes: dict) -> dict:
    """Create or overwrite a shipment status record."""
    item = {
        "PK": f"{PK_SHIPMENT}{shipment_id}",
        "SK": SK_STATUS,
        **attributes,
    }
    return put_item(item)


def update_shipment(shipment_id: str, updates: dict[str, Any]) -> dict:
    """Update specific fields on a shipment status record."""
    return update_item(f"{PK_SHIPMENT}{shipment_id}", SK_STATUS, updates)


def put_shipment_event(shipment_id: str, timestamp: str, event_data: dict) -> dict:
    """Store a shipment lifecycle event."""
    item = {
        "PK": f"{PK_SHIPMENT}{shipment_id}",
        "SK": f"{SK_EVENT_PREFIX}{timestamp}",
        **event_data,
    }
    return put_item(item)


def get_shipment_events(shipment_id: str) -> list[dict]:
    """Get all events for a shipment, ordered by timestamp."""
    return query_items(f"{PK_SHIPMENT}{shipment_id}", SK_EVENT_PREFIX)
