"""Orchestrator Agent — classifies shipment requests and routes to Step Functions.

Uses the SLM-first with LLM fallback pattern:
- Nova Micro (SLM) for initial classification
- Claude Sonnet 4.6 (LLM) fallback when confidence < 0.75

The agent receives a shipment request payload and returns:
- classification (e.g., "transactional")
- confidence score
- model_tier used ("SLM" or "LLM")
- execution_arn from Step Functions
"""

import json
import os
import time
import uuid
import logging
from typing import Any

import boto3
from strands import Agent

from shared.constants import (
    CONFIDENCE_THRESHOLD,
    ORCHESTRATOR_MODEL_SLM,
    LLM_MODEL,
)

logger = logging.getLogger(__name__)

# System prompt for the Orchestrator Agent
ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are the Orchestrator Agent for the TMS MVP demo. Your role is to classify "
    "incoming shipment requests and route them to the Step Functions shipment lifecycle "
    "workflow. Use the SLM tier (Nova Micro) for classification. If confidence is below "
    "0.75, escalate to the LLM (Claude Sonnet 4.6). Return the classification result "
    "with confidence score and model tier used."
)


def start_shipment_workflow(payload: dict) -> dict:
    """Trigger the Step Functions shipment lifecycle workflow.

    This tool is wired to the Orchestrator Agent and starts a new
    Step Functions execution with the provided shipment payload.

    Args:
        payload: Shipment request containing origin, destination,
                 weight_kg, commodity, delivery_date.

    Returns:
        dict with execution_arn and start_date.
    """
    state_machine_arn = os.environ.get("STATE_MACHINE_ARN", "")
    if not state_machine_arn:
        logger.error("STATE_MACHINE_ARN environment variable is not set")
        return {"error": "STATE_MACHINE_ARN not configured", "execution_arn": None}

    sfn_client = boto3.client("stepfunctions")

    execution_name = f"shipment-{uuid.uuid4().hex[:12]}"

    try:
        response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=execution_name,
            input=json.dumps(payload),
        )
        return {
            "execution_arn": response["executionArn"],
            "start_date": response["startDate"].isoformat(),
        }
    except Exception as e:
        logger.error("Failed to start Step Functions execution: %s", str(e))
        return {"error": str(e), "execution_arn": None}


def _create_agent(model_id: str) -> Agent:
    """Create a Strands Agent configured with the given model.

    Args:
        model_id: Bedrock model identifier (SLM or LLM).

    Returns:
        Configured Strands Agent instance.
    """
    return Agent(
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        model=model_id,
        tools=[start_shipment_workflow],
    )


def _parse_classification_response(response: Any) -> dict:
    """Parse the agent response to extract classification and confidence.

    The agent is expected to return a JSON-like response with
    classification and confidence fields.

    Args:
        response: Raw agent response object.

    Returns:
        dict with classification and confidence keys.
    """
    # Extract text content from agent response
    response_text = str(response)

    # Attempt to parse as JSON
    try:
        # Try to find JSON in the response
        start_idx = response_text.find("{")
        end_idx = response_text.rfind("}") + 1
        if start_idx != -1 and end_idx > start_idx:
            parsed = json.loads(response_text[start_idx:end_idx])
            return {
                "classification": parsed.get("classification", "transactional"),
                "confidence": float(parsed.get("confidence", 0.0)),
            }
    except (json.JSONDecodeError, ValueError):
        pass

    # Default: classify as transactional with moderate confidence
    return {
        "classification": "transactional",
        "confidence": 0.80,
    }


def invoke_orchestrator_agent(payload: dict) -> dict:
    """Orchestrate the SLM-first with LLM fallback classification flow.

    1. Invoke Nova Micro (SLM) for classification
    2. Check confidence against threshold (0.75)
    3. If below threshold, re-invoke with Claude Sonnet 4.6 (LLM)
    4. Trigger Step Functions workflow via start_shipment_workflow tool
    5. Return classification, confidence, model_tier, execution_arn

    Args:
        payload: Shipment request dict with keys:
                 origin, destination, weight_kg, commodity, delivery_date.

    Returns:
        dict with classification, confidence, model_tier, execution_arn.
    """
    classification_prompt = (
        f"Classify the following shipment request and provide your classification "
        f"and confidence score as JSON: {json.dumps(payload)}\n\n"
        f"Respond with JSON: {{\"classification\": \"<type>\", \"confidence\": <0.0-1.0>}}"
    )

    # Step 1: Try SLM (Nova Micro) first
    start_time = time.time()
    try:
        slm_agent = _create_agent(ORCHESTRATOR_MODEL_SLM)
        slm_response = slm_agent(classification_prompt)
        slm_result = _parse_classification_response(slm_response)
    except Exception as e:
        logger.warning("SLM invocation failed: %s. Falling back to LLM.", str(e))
        slm_result = {"classification": "unknown", "confidence": 0.0}

    slm_latency_ms = int((time.time() - start_time) * 1000)

    # Step 2: Check confidence threshold
    if slm_result["confidence"] >= CONFIDENCE_THRESHOLD:
        # SLM result is acceptable — use it
        model_tier = "SLM"
        model_id = ORCHESTRATOR_MODEL_SLM
        classification = slm_result["classification"]
        confidence = slm_result["confidence"]
        latency_ms = slm_latency_ms
    else:
        # Step 3: Escalate to LLM (Claude Sonnet 4.6)
        logger.info(
            "SLM confidence %.2f below threshold %.2f, escalating to LLM",
            slm_result["confidence"],
            CONFIDENCE_THRESHOLD,
        )
        start_time = time.time()
        try:
            llm_agent = _create_agent(LLM_MODEL)
            llm_response = llm_agent(classification_prompt)
            llm_result = _parse_classification_response(llm_response)
        except Exception as e:
            logger.error("LLM invocation failed: %s", str(e))
            return {
                "classification": slm_result["classification"],
                "confidence": slm_result["confidence"],
                "model_tier": "SLM",
                "model_id": ORCHESTRATOR_MODEL_SLM,
                "latency_ms": slm_latency_ms,
                "execution_arn": None,
                "error": f"LLM fallback failed: {str(e)}",
            }

        llm_latency_ms = int((time.time() - start_time) * 1000)
        model_tier = "LLM"
        model_id = LLM_MODEL
        classification = llm_result["classification"]
        confidence = llm_result["confidence"]
        latency_ms = slm_latency_ms + llm_latency_ms

    # Step 4: Trigger Step Functions workflow
    workflow_result = start_shipment_workflow(payload)
    execution_arn = workflow_result.get("execution_arn")

    return {
        "classification": classification,
        "confidence": confidence,
        "model_tier": model_tier,
        "model_id": model_id,
        "latency_ms": latency_ms,
        "execution_arn": execution_arn,
    }
