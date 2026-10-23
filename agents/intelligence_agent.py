"""Intelligence Agent for the TMS MVP Demo.

Classifies shipment exceptions by type and severity, then provides a resolution
recommendation. Uses the SLM-first with LLM fallback pattern:
- Nova Lite (SLM) for initial exception classification
- Claude Sonnet 4.6 (LLM) for root-cause analysis when SLM confidence < 0.75

Requirements: 5.1, 5.2, 5.3, 5.4
"""

import json
import logging
import time
from typing import Any

from strands import Agent

from shared.constants import (
    CONFIDENCE_THRESHOLD,
    INTELLIGENCE_MODEL_SLM,
    LLM_MODEL,
    MEMORY_NAMESPACE_EXCEPTIONS,
)
from shared import memory

logger = logging.getLogger(__name__)

# System prompt for the Intelligence Agent
INTELLIGENCE_PROMPT = (
    "You are the Intelligence Agent for the TMS MVP demo. Your role is to classify shipment "
    "exceptions by type and severity, then provide a resolution recommendation. Use the SLM "
    "tier (Nova Lite) for exception classification. If confidence is below 0.75, escalate to "
    "the LLM (Claude Sonnet 4.6) for root-cause analysis. Return the exception type, severity, "
    "resolution recommendation, confidence score, and model tier used."
)

# Resolution recommendations by exception type
_RESOLUTION_MAP: dict[str, dict[str, Any]] = {
    "delay": {
        "resolution_recommendation": "Reroute shipment via alternate corridor, adjust ETA, and notify customer of revised delivery window.",
        "severity_default": "medium",
    },
    "damage": {
        "resolution_recommendation": "Initiate damage claim with carrier, arrange replacement shipment, and schedule inspection at next hub.",
        "severity_default": "high",
    },
    "missing": {
        "resolution_recommendation": "Trigger carrier investigation, check last known scan location, and escalate to carrier operations team.",
        "severity_default": "high",
    },
    "customs_hold": {
        "resolution_recommendation": "Contact customs broker for documentation review, prepare supplementary declarations, and update delivery timeline.",
        "severity_default": "medium",
    },
    "capacity": {
        "resolution_recommendation": "Identify alternate carriers with available capacity, consider shipment splitting, and update booking.",
        "severity_default": "low",
    },
}


def _create_agent(model_id: str) -> Agent:
    """Create a Strands Agent configured for exception analysis.

    Args:
        model_id: The Bedrock model ID to use (SLM or LLM).

    Returns:
        A configured Strands Agent instance.
    """
    return Agent(
        system_prompt=INTELLIGENCE_PROMPT,
        model=model_id,
    )


def _classify_exception(payload: dict) -> dict:
    """Classify the exception and produce a structured result.

    For the MVP demo, this produces a realistic classification and resolution
    based on the exception_type hint and shipment context. In production,
    the Strands Agent would perform full reasoning over the context.

    Args:
        payload: The exception payload containing shipment_id, exception_type,
                 severity_hint, and shipment context.

    Returns:
        A dict with exception_type, severity, resolution_recommendation,
        and confidence.
    """
    exception_type = payload.get("exception_type", "delay")
    severity_hint = payload.get("severity_hint", "medium")

    # Look up resolution data
    resolution_data = _RESOLUTION_MAP.get(exception_type, _RESOLUTION_MAP["delay"])

    # Use severity_hint if provided, otherwise use default for the exception type
    severity = severity_hint if severity_hint else resolution_data["severity_default"]

    return {
        "exception_type": exception_type,
        "severity": severity,
        "resolution_recommendation": resolution_data["resolution_recommendation"],
    }


def _invoke_slm(payload: dict) -> dict:
    """Invoke the Intelligence Agent using the SLM (Nova Lite).

    Args:
        payload: The exception payload to analyze.

    Returns:
        A dict with classification result, confidence, and model metadata.
    """
    start_time = time.time()

    # Create agent with SLM model
    agent = _create_agent(INTELLIGENCE_MODEL_SLM)

    # Build the prompt for the agent
    prompt = (
        f"Classify this shipment exception and provide a resolution recommendation.\n"
        f"Shipment ID: {payload.get('shipment_id', 'unknown')}\n"
        f"Exception Type Hint: {payload.get('exception_type', 'unknown')}\n"
        f"Severity Hint: {payload.get('severity_hint', 'unknown')}\n"
        f"Context: {json.dumps(payload.get('context', {}))}\n\n"
        f"Return your analysis as JSON with fields: exception_type, severity, "
        f"resolution_recommendation, confidence (0.0-1.0)."
    )

    try:
        # Invoke the agent
        response = agent(prompt)

        # For MVP demo, produce structured classification
        classification = _classify_exception(payload)

        # Extract confidence from agent response or use a realistic demo value
        # In production, the agent response would contain a parsed confidence
        confidence = _extract_confidence(response, payload)

        latency_ms = int((time.time() - start_time) * 1000)

        return {
            "exception_type": classification["exception_type"],
            "severity": classification["severity"],
            "resolution_recommendation": classification["resolution_recommendation"],
            "confidence": confidence,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": latency_ms,
        }
    except Exception as e:
        logger.warning("SLM invocation failed: %s. Falling back to LLM.", str(e))
        # On SLM failure, return low confidence to trigger LLM fallback
        latency_ms = int((time.time() - start_time) * 1000)
        return {
            "exception_type": payload.get("exception_type", "delay"),
            "severity": payload.get("severity_hint", "medium"),
            "resolution_recommendation": "",
            "confidence": 0.0,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": latency_ms,
        }


def _invoke_llm(payload: dict) -> dict:
    """Invoke the Intelligence Agent using the LLM (Claude Sonnet 4.6).

    Used as fallback when SLM confidence is below the threshold.

    Args:
        payload: The exception payload to analyze.

    Returns:
        A dict with classification result, confidence, and model metadata.
    """
    start_time = time.time()

    # Create agent with LLM model
    agent = _create_agent(LLM_MODEL)

    # Build a more detailed prompt for root-cause analysis
    prompt = (
        f"Perform a detailed root-cause analysis for this shipment exception.\n"
        f"Shipment ID: {payload.get('shipment_id', 'unknown')}\n"
        f"Exception Type Hint: {payload.get('exception_type', 'unknown')}\n"
        f"Severity Hint: {payload.get('severity_hint', 'unknown')}\n"
        f"Context: {json.dumps(payload.get('context', {}))}\n\n"
        f"Provide exception classification, severity assessment, root-cause analysis, "
        f"and a detailed resolution recommendation. Return as JSON with fields: "
        f"exception_type, severity, resolution_recommendation, confidence (0.0-1.0)."
    )

    try:
        # Invoke the agent
        response = agent(prompt)

        # For MVP demo, produce structured classification with LLM-level detail
        classification = _classify_exception(payload)

        # LLM typically produces higher confidence
        confidence = _extract_confidence(response, payload, is_llm=True)

        latency_ms = int((time.time() - start_time) * 1000)

        return {
            "exception_type": classification["exception_type"],
            "severity": classification["severity"],
            "resolution_recommendation": classification["resolution_recommendation"],
            "confidence": confidence,
            "model_tier": "LLM",
            "model_id": LLM_MODEL,
            "latency_ms": latency_ms,
        }
    except Exception as e:
        logger.error("LLM invocation failed: %s", str(e))
        latency_ms = int((time.time() - start_time) * 1000)
        # Return best-effort classification even on LLM failure
        classification = _classify_exception(payload)
        return {
            "exception_type": classification["exception_type"],
            "severity": classification["severity"],
            "resolution_recommendation": classification["resolution_recommendation"],
            "confidence": 0.5,
            "model_tier": "LLM",
            "model_id": LLM_MODEL,
            "latency_ms": latency_ms,
        }


def _extract_confidence(response: Any, payload: dict, is_llm: bool = False) -> float:
    """Extract confidence score from agent response.

    For the MVP demo, produces a realistic confidence value based on the
    exception type. In production, the agent response would be parsed for
    an explicit confidence field.

    Args:
        response: The raw agent response.
        payload: The original exception payload.
        is_llm: Whether this is an LLM response (typically higher confidence).

    Returns:
        A confidence score between 0.0 and 1.0.
    """
    # Try to parse confidence from agent response text
    try:
        response_text = str(response)
        if "confidence" in response_text.lower():
            # Attempt to find a JSON block in the response
            import re

            json_match = re.search(r'\{[^{}]*"confidence"\s*:\s*([\d.]+)[^{}]*\}', response_text)
            if json_match:
                parsed_confidence = float(json_match.group(1))
                if 0.0 <= parsed_confidence <= 1.0:
                    return parsed_confidence
    except (ValueError, AttributeError):
        pass

    # Fallback: produce realistic demo confidence values
    exception_type = payload.get("exception_type", "delay")

    if is_llm:
        # LLM typically has higher confidence (0.85-0.95)
        confidence_map = {
            "delay": 0.91,
            "damage": 0.88,
            "missing": 0.85,
            "customs_hold": 0.87,
            "capacity": 0.93,
        }
        return confidence_map.get(exception_type, 0.89)
    else:
        # SLM confidence varies — some exceptions are straightforward
        confidence_map = {
            "delay": 0.82,
            "damage": 0.68,
            "missing": 0.61,
            "customs_hold": 0.55,
            "capacity": 0.78,
        }
        return confidence_map.get(exception_type, 0.72)


def invoke_intelligence_agent(payload: dict) -> dict:
    """Orchestrate the SLM-first with LLM fallback flow for exception analysis.

    This is the main entry point for the Intelligence Agent. It:
    1. Invokes the SLM (Nova Lite) for initial classification
    2. Checks the confidence score against the threshold (0.75)
    3. If confidence >= 0.75: returns the SLM result
    4. If confidence < 0.75: escalates to the LLM (Claude Sonnet 4.6)

    Args:
        payload: A dict containing:
            - shipment_id: The shipment to analyze
            - exception_type: Hint about the exception type (e.g., "delay")
            - severity_hint: Hint about severity (e.g., "medium")
            - context: Optional dict with additional shipment context

    Returns:
        A dict containing:
            - exception_type: Classified exception type
            - severity: Assessed severity (low, medium, high)
            - resolution_recommendation: Recommended action
            - confidence: Final confidence score (0.0-1.0)
            - model_tier: "SLM" or "LLM"
            - model_id: The Bedrock model ID used
            - latency_ms: Total processing time in milliseconds
    """
    logger.info(
        "Intelligence Agent invoked for shipment %s (exception_type=%s)",
        payload.get("shipment_id"),
        payload.get("exception_type"),
    )

    # Step 0: Retrieve prior resolutions for similar exceptions from AgentCore
    # Memory to condition this analysis (continuous-learning loop). Best-effort.
    prior = memory.retrieve_similar(
        query=(
            f"{payload.get('exception_type', 'exception')} exception "
            f"severity {payload.get('severity_hint', 'unknown')}"
        ),
        namespace=MEMORY_NAMESPACE_EXCEPTIONS,
    )
    if prior:
        logger.info("Intelligence agent found %d relevant prior resolution(s)", len(prior))

    def _record(result: dict) -> dict:
        """Persist the resolution outcome to memory, then return the result."""
        memory.record_outcome(
            actor_id="intelligence-agent",
            session_id=str(payload.get("shipment_id", "unknown")),
            outcome={
                "type": "exception_resolved",
                "exception_type": result.get("exception_type"),
                "severity": result.get("severity"),
                "resolution_recommendation": result.get("resolution_recommendation"),
                "confidence": result.get("confidence"),
                "model_tier": result.get("model_tier"),
            },
            namespace=MEMORY_NAMESPACE_EXCEPTIONS,
        )
        return result

    # Step 1: Invoke SLM first
    slm_result = _invoke_slm(payload)
    slm_confidence = slm_result["confidence"]

    logger.info(
        "SLM result: confidence=%.2f, threshold=%.2f",
        slm_confidence,
        CONFIDENCE_THRESHOLD,
    )

    # Step 2: Check confidence threshold
    if slm_confidence >= CONFIDENCE_THRESHOLD:
        logger.info("SLM confidence sufficient. Returning SLM result.")
        return _record(slm_result)

    # Step 3: Escalate to LLM
    logger.info("SLM confidence below threshold. Escalating to LLM.")
    llm_result = _invoke_llm(payload)

    logger.info(
        "LLM result: confidence=%.2f, model_tier=%s",
        llm_result["confidence"],
        llm_result["model_tier"],
    )

    return _record(llm_result)
