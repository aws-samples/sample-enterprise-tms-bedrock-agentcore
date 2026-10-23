"""Smoke test: verify DynamoDB seed data exists post-deployment.

Validates: Requirements 14.1, 14.2
- Carrier records (PK starts with CARRIER#) exist
- Rate records (PK starts with RATE#) exist
"""

import sys
import os

import boto3
import pytest

# Add project root to path so we can import shared constants
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.constants import TABLE_NAME, PK_CARRIER, PK_RATE


@pytest.fixture
def dynamodb_table():
    """Create a DynamoDB table resource for the TMS MVP table."""
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(TABLE_NAME)


class TestDynamoSeedData:
    """Verify that seed data was loaded into DynamoDB during deployment."""

    def test_carrier_records_exist(self, dynamodb_table):
        """Verify at least one carrier record exists with PK starting with CARRIER#."""
        response = dynamodb_table.scan(
            FilterExpression="begins_with(PK, :prefix)",
            ExpressionAttributeValues={":prefix": PK_CARRIER},
            Limit=10,
        )

        items = response.get("Items", [])
        assert len(items) > 0, (
            f"Expected carrier records (PK starts with '{PK_CARRIER}') "
            f"in table '{TABLE_NAME}', but found none."
        )

    def test_carrier_records_have_required_fields(self, dynamodb_table):
        """Verify carrier records contain name, service_type, and coverage_region."""
        response = dynamodb_table.scan(
            FilterExpression="begins_with(PK, :prefix)",
            ExpressionAttributeValues={":prefix": PK_CARRIER},
            Limit=10,
        )

        items = response.get("Items", [])
        assert len(items) > 0, "No carrier records found."

        for item in items:
            assert "name" in item, f"Carrier record missing 'name': {item.get('PK')}"
            assert "service_type" in item, (
                f"Carrier record missing 'service_type': {item.get('PK')}"
            )
            assert "coverage_region" in item, (
                f"Carrier record missing 'coverage_region': {item.get('PK')}"
            )

    def test_rate_records_exist(self, dynamodb_table):
        """Verify at least one rate record exists with PK starting with RATE#."""
        response = dynamodb_table.scan(
            FilterExpression="begins_with(PK, :prefix)",
            ExpressionAttributeValues={":prefix": PK_RATE},
            Limit=10,
        )

        items = response.get("Items", [])
        assert len(items) > 0, (
            f"Expected rate records (PK starts with '{PK_RATE}') "
            f"in table '{TABLE_NAME}', but found none."
        )

    def test_rate_records_have_required_fields(self, dynamodb_table):
        """Verify rate records contain rate_usd, transit_days, and service_type."""
        response = dynamodb_table.scan(
            FilterExpression="begins_with(PK, :prefix)",
            ExpressionAttributeValues={":prefix": PK_RATE},
            Limit=10,
        )

        items = response.get("Items", [])
        assert len(items) > 0, "No rate records found."

        for item in items:
            assert "rate_usd" in item, (
                f"Rate record missing 'rate_usd': {item.get('PK')}"
            )
            assert "transit_days" in item, (
                f"Rate record missing 'transit_days': {item.get('PK')}"
            )
            assert "service_type" in item, (
                f"Rate record missing 'service_type': {item.get('PK')}"
            )

    def test_minimum_carrier_count(self, dynamodb_table):
        """Verify at least 3 carrier records exist (seed data should have 5)."""
        response = dynamodb_table.scan(
            FilterExpression="begins_with(PK, :prefix)",
            ExpressionAttributeValues={":prefix": PK_CARRIER},
        )

        items = response.get("Items", [])
        assert len(items) >= 3, (
            f"Expected at least 3 carrier records, found {len(items)}."
        )

    def test_minimum_rate_count(self, dynamodb_table):
        """Verify at least 5 rate records exist (seed data should have 10-15)."""
        response = dynamodb_table.scan(
            FilterExpression="begins_with(PK, :prefix)",
            ExpressionAttributeValues={":prefix": PK_RATE},
        )

        items = response.get("Items", [])
        assert len(items) >= 5, (
            f"Expected at least 5 rate records, found {len(items)}."
        )
