"""Smoke test: trigger Step Functions workflow and verify terminal state.

Validates: Requirements 14.1, 14.3
- Step Functions workflow can be triggered with a test payload
- Workflow reaches a terminal state (SUCCEEDED)
"""

import json
import os
import sys
import time

import boto3
import pytest

# Add project root to path so we can import shared constants
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.constants import TABLE_NAME

# State machine ARN read from environment variable
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")

# Timeout configuration for waiting on execution
MAX_WAIT_SECONDS = 120
POLL_INTERVAL_SECONDS = 5


@pytest.fixture
def sfn_client():
    """Create a Step Functions client."""
    return boto3.client("stepfunctions")


@pytest.fixture
def test_shipment_payload():
    """A minimal valid shipment payload for the workflow."""
    return {
        "origin": "Chicago, IL",
        "destination": "New York, NY",
        "weight_kg": 500,
        "commodity": "Electronics",
        "delivery_date": "2025-02-15",
    }


class TestWorkflowExecution:
    """Verify that the Step Functions workflow can execute end-to-end."""

    @pytest.mark.skipif(
        not STATE_MACHINE_ARN,
        reason="STATE_MACHINE_ARN environment variable not set",
    )
    def test_workflow_starts_successfully(
        self, sfn_client, test_shipment_payload
    ):
        """Verify that the workflow can be started with a test payload."""
        response = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=json.dumps(test_shipment_payload),
        )

        execution_arn = response["executionArn"]
        assert execution_arn, "Expected a valid execution ARN."

        # Verify execution is running or already succeeded
        describe = sfn_client.describe_execution(executionArn=execution_arn)
        status = describe["status"]
        assert status in ("RUNNING", "SUCCEEDED"), (
            f"Expected execution to be RUNNING or SUCCEEDED, got '{status}'."
        )

    @pytest.mark.skipif(
        not STATE_MACHINE_ARN,
        reason="STATE_MACHINE_ARN environment variable not set",
    )
    def test_workflow_reaches_terminal_state(
        self, sfn_client, test_shipment_payload
    ):
        """Verify the workflow reaches SUCCEEDED terminal state within timeout."""
        response = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=json.dumps(test_shipment_payload),
        )

        execution_arn = response["executionArn"]
        terminal_states = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}

        # Poll for terminal state with timeout
        elapsed = 0
        status = "RUNNING"

        while elapsed < MAX_WAIT_SECONDS:
            describe = sfn_client.describe_execution(executionArn=execution_arn)
            status = describe["status"]

            if status in terminal_states:
                break

            time.sleep(POLL_INTERVAL_SECONDS)
            elapsed += POLL_INTERVAL_SECONDS

        assert status == "SUCCEEDED", (
            f"Expected workflow to reach SUCCEEDED state within "
            f"{MAX_WAIT_SECONDS}s, but got '{status}'. "
            f"Execution ARN: {execution_arn}"
        )

    @pytest.mark.skipif(
        not STATE_MACHINE_ARN,
        reason="STATE_MACHINE_ARN environment variable not set",
    )
    def test_workflow_output_contains_shipment_id(
        self, sfn_client, test_shipment_payload
    ):
        """Verify the workflow output includes a shipment_id on success."""
        response = sfn_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=json.dumps(test_shipment_payload),
        )

        execution_arn = response["executionArn"]
        terminal_states = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}

        # Poll for terminal state with timeout
        elapsed = 0
        status = "RUNNING"

        while elapsed < MAX_WAIT_SECONDS:
            describe = sfn_client.describe_execution(executionArn=execution_arn)
            status = describe["status"]

            if status in terminal_states:
                break

            time.sleep(POLL_INTERVAL_SECONDS)
            elapsed += POLL_INTERVAL_SECONDS

        if status != "SUCCEEDED":
            pytest.skip(
                f"Workflow did not succeed (status: {status}), "
                f"skipping output validation."
            )

        output = json.loads(describe.get("output", "{}"))
        assert "shipment_id" in output, (
            f"Expected 'shipment_id' in workflow output, got: {output}"
        )
