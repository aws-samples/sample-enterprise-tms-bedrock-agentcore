"""Property-based tests for agent routing logic.

Tests the SLM-first with LLM fallback pattern across all 3 agents,
agent response structure validation, and exception classification completeness.

**Validates: Requirements 2.1, 2.4, 4.1, 4.2, 4.3, 4.5, 5.1, 5.2, 5.4, 13.2, 13.3**

Uses the Hypothesis library to generate random inputs with mocked Bedrock
model invocations (no real AI calls).
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.constants import (
    CONFIDENCE_THRESHOLD,
    INTELLIGENCE_MODEL_SLM,
    LLM_MODEL,
    ORCHESTRATOR_MODEL_SLM,
    PLANNING_MODEL_SLM,
)
from agents.planning_agent import invoke_planning_agent
from agents.intelligence_agent import invoke_intelligence_agent
from agents.orchestrator_agent import invoke_orchestrator_agent


# --- Strategies ---

# Confidence values: floats between 0.0 and 1.0
confidence_strategy = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

# Confidence values at or above threshold (SLM sufficient)
high_confidence_strategy = st.floats(
    min_value=CONFIDENCE_THRESHOLD, max_value=1.0, allow_nan=False, allow_infinity=False
)

# Confidence values below threshold (requires LLM fallback)
low_confidence_strategy = st.floats(
    min_value=0.0, max_value=CONFIDENCE_THRESHOLD - 0.001, allow_nan=False, allow_infinity=False
)

# Exception types for Intelligence Agent
exception_type_strategy = st.sampled_from(["delay", "damage", "missing", "customs_hold", "capacity"])

# Severity levels
severity_strategy = st.sampled_from(["low", "medium", "high"])

# City names for route payloads
city_strategy = st.sampled_from([
    "Chicago, IL", "New York, NY", "Los Angeles, CA", "Dallas, TX",
    "Seattle, WA", "Denver, CO", "Miami, FL", "Atlanta, GA",
    "Boston, MA", "Phoenix, AZ",
])

# Shipment weight in kg
weight_strategy = st.floats(min_value=1.0, max_value=50000.0, allow_nan=False, allow_infinity=False)


# ===========================================================================
# Property 1: SLM-First with LLM Fallback Routing
# ===========================================================================


class TestSLMFirstWithLLMFallbackRouting:
    """**Validates: Requirements 2.1, 2.4, 4.1, 4.2, 4.3, 5.4, 13.2, 13.3**

    For any agent request (Orchestrator, Planning, or Intelligence), if the SLM
    confidence score is at or above the Confidence_Threshold (0.75), the system
    SHALL return the SLM result without invoking the LLM; if the SLM confidence
    score is below the Confidence_Threshold, the system SHALL escalate to the LLM
    and return the LLM result.
    """

    @given(confidence=high_confidence_strategy)
    @settings(max_examples=50)
    @patch("agents.planning_agent._invoke_agent")
    def test_planning_agent_uses_slm_when_confidence_sufficient(
        self, mock_invoke_agent, confidence
    ):
        """Planning Agent returns SLM result when confidence >= 0.75."""
        mock_invoke_agent.return_value = {
            "route": {
                "stops": ["Chicago, IL", "Indianapolis, IN", "New York, NY"],
                "distance_km": 1200.0,
                "transit_hours": 14.0,
            },
            "confidence": confidence,
        }

        payload = {
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": 500.0,
            "shipment_id": "test-ship-001",
        }

        result = invoke_planning_agent(payload)

        assert result["model_tier"] == "SLM"
        assert result["confidence"] == confidence
        assert result["model_id"] == PLANNING_MODEL_SLM
        # SLM should be called exactly once (no LLM call). Third arg is the
        # empty prior-context string from AgentCore Memory (no-op in tests).
        mock_invoke_agent.assert_called_once_with(PLANNING_MODEL_SLM, payload, "")

    @given(confidence=low_confidence_strategy)
    @settings(max_examples=50)
    @patch("agents.planning_agent._invoke_agent")
    def test_planning_agent_escalates_to_llm_when_confidence_low(
        self, mock_invoke_agent, confidence
    ):
        """Planning Agent escalates to LLM when SLM confidence < 0.75."""
        llm_confidence = 0.92

        def side_effect(model_id, payload, prior_context=""):
            if model_id == PLANNING_MODEL_SLM:
                return {
                    "route": {"stops": [], "distance_km": 0.0, "transit_hours": 0.0},
                    "confidence": confidence,
                }
            else:
                return {
                    "route": {
                        "stops": ["Chicago, IL", "Columbus, OH", "New York, NY"],
                        "distance_km": 1150.0,
                        "transit_hours": 13.5,
                    },
                    "confidence": llm_confidence,
                }

        mock_invoke_agent.side_effect = side_effect

        payload = {
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": 500.0,
            "shipment_id": "test-ship-002",
        }

        result = invoke_planning_agent(payload)

        assert result["model_tier"] == "LLM"
        assert result["confidence"] == llm_confidence
        assert result["model_id"] == LLM_MODEL
        # Should be called twice: once for SLM, once for LLM
        assert mock_invoke_agent.call_count == 2

    @given(confidence=high_confidence_strategy)
    @settings(max_examples=50)
    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_intelligence_agent_uses_slm_when_confidence_sufficient(
        self, mock_slm, mock_llm, confidence
    ):
        """Intelligence Agent returns SLM result when confidence >= 0.75."""
        mock_slm.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "Reroute shipment via alternate corridor.",
            "confidence": confidence,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 150,
        }

        payload = {
            "shipment_id": "test-ship-003",
            "exception_type": "delay",
            "severity_hint": "medium",
        }

        result = invoke_intelligence_agent(payload)

        assert result["model_tier"] == "SLM"
        assert result["confidence"] == confidence
        mock_slm.assert_called_once_with(payload)
        mock_llm.assert_not_called()

    @given(confidence=low_confidence_strategy)
    @settings(max_examples=50)
    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_intelligence_agent_escalates_to_llm_when_confidence_low(
        self, mock_slm, mock_llm, confidence
    ):
        """Intelligence Agent escalates to LLM when SLM confidence < 0.75."""
        llm_confidence = 0.91

        mock_slm.return_value = {
            "exception_type": "damage",
            "severity": "high",
            "resolution_recommendation": "",
            "confidence": confidence,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 100,
        }
        mock_llm.return_value = {
            "exception_type": "damage",
            "severity": "high",
            "resolution_recommendation": "Initiate damage claim with carrier.",
            "confidence": llm_confidence,
            "model_tier": "LLM",
            "model_id": LLM_MODEL,
            "latency_ms": 450,
        }

        payload = {
            "shipment_id": "test-ship-004",
            "exception_type": "damage",
            "severity_hint": "high",
        }

        result = invoke_intelligence_agent(payload)

        assert result["model_tier"] == "LLM"
        assert result["confidence"] == llm_confidence
        mock_slm.assert_called_once_with(payload)
        mock_llm.assert_called_once_with(payload)

    @given(confidence=high_confidence_strategy)
    @settings(max_examples=50)
    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_orchestrator_agent_uses_slm_when_confidence_sufficient(
        self, mock_create_agent, mock_workflow, confidence
    ):
        """Orchestrator Agent returns SLM result when confidence >= 0.75."""
        mock_workflow.return_value = {"execution_arn": "arn:aws:states:us-east-1:123:execution:test", "start_date": "2024-01-01T00:00:00"}

        mock_agent_instance = MagicMock()
        mock_response = f'{{"classification": "transactional", "confidence": {confidence}}}'
        mock_agent_instance.return_value = mock_response
        mock_create_agent.return_value = mock_agent_instance

        payload = {
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": 500.0,
            "commodity": "Electronics",
            "delivery_date": "2024-06-15",
        }

        result = invoke_orchestrator_agent(payload)

        assert result["model_tier"] == "SLM"
        assert result["confidence"] == confidence
        # Only one agent created (SLM), no LLM escalation
        mock_create_agent.assert_called_once_with(ORCHESTRATOR_MODEL_SLM)

    @given(confidence=low_confidence_strategy)
    @settings(max_examples=50)
    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_orchestrator_agent_escalates_to_llm_when_confidence_low(
        self, mock_create_agent, mock_workflow, confidence
    ):
        """Orchestrator Agent escalates to LLM when SLM confidence < 0.75."""
        mock_workflow.return_value = {"execution_arn": "arn:aws:states:us-east-1:123:execution:test", "start_date": "2024-01-01T00:00:00"}

        llm_confidence = 0.92
        call_count = {"n": 0}

        def create_agent_side_effect(model_id):
            instance = MagicMock()
            call_count["n"] += 1
            if call_count["n"] == 1:
                # SLM returns low confidence
                instance.return_value = f'{{"classification": "transactional", "confidence": {confidence}}}'
            else:
                # LLM returns high confidence
                instance.return_value = f'{{"classification": "transactional", "confidence": {llm_confidence}}}'
            return instance

        mock_create_agent.side_effect = create_agent_side_effect

        payload = {
            "origin": "Chicago, IL",
            "destination": "New York, NY",
            "weight_kg": 500.0,
            "commodity": "Electronics",
            "delivery_date": "2024-06-15",
        }

        result = invoke_orchestrator_agent(payload)

        assert result["model_tier"] == "LLM"
        assert result["confidence"] == llm_confidence
        # Two agents created: SLM first, then LLM
        assert mock_create_agent.call_count == 2


# ===========================================================================
# Property 7: Agent Responses Include Model Tier Indicator
# ===========================================================================


class TestAgentResponsesIncludeModelTierIndicator:
    """**Validates: Requirements 4.5, 13.1**

    For any response from the Planning Agent or Intelligence Agent, the response
    SHALL include both the domain result and an indication of which model tier
    (SLM or LLM) produced the result, along with the confidence score.
    """

    @given(
        confidence=confidence_strategy,
        origin=city_strategy,
        destination=city_strategy,
        weight=weight_strategy,
    )
    @settings(max_examples=50)
    @patch("agents.planning_agent._invoke_agent")
    def test_planning_agent_response_includes_model_tier_and_confidence(
        self, mock_invoke_agent, confidence, origin, destination, weight
    ):
        """Planning Agent response always contains model_tier and confidence."""
        assume(origin != destination)

        def side_effect(model_id, payload, prior_context=""):
            return {
                "route": {
                    "stops": [origin, destination],
                    "distance_km": 800.0,
                    "transit_hours": 10.0,
                },
                "confidence": confidence,
            }

        mock_invoke_agent.side_effect = side_effect

        payload = {
            "origin": origin,
            "destination": destination,
            "weight_kg": weight,
            "shipment_id": "test-ship-prop7",
        }

        result = invoke_planning_agent(payload)

        # model_tier must be present and valid
        assert "model_tier" in result
        assert result["model_tier"] in ("SLM", "LLM")

        # confidence must be present and in valid range
        assert "confidence" in result
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0

        # Domain result (route) must be present
        assert "route" in result
        assert isinstance(result["route"], dict)

    @given(
        confidence=confidence_strategy,
        exception_type=exception_type_strategy,
        severity=severity_strategy,
    )
    @settings(max_examples=50)
    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_intelligence_agent_response_includes_model_tier_and_confidence(
        self, mock_slm, mock_llm, confidence, exception_type, severity
    ):
        """Intelligence Agent response always contains model_tier and confidence."""
        mock_slm.return_value = {
            "exception_type": exception_type,
            "severity": severity,
            "resolution_recommendation": "Take corrective action.",
            "confidence": confidence,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 120,
        }
        mock_llm.return_value = {
            "exception_type": exception_type,
            "severity": severity,
            "resolution_recommendation": "Detailed corrective action with root cause analysis.",
            "confidence": 0.91,
            "model_tier": "LLM",
            "model_id": LLM_MODEL,
            "latency_ms": 450,
        }

        payload = {
            "shipment_id": "test-ship-prop7-int",
            "exception_type": exception_type,
            "severity_hint": severity,
        }

        result = invoke_intelligence_agent(payload)

        # model_tier must be present and valid
        assert "model_tier" in result
        assert result["model_tier"] in ("SLM", "LLM")

        # confidence must be present and in valid range
        assert "confidence" in result
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0

        # model_id must be present
        assert "model_id" in result


# ===========================================================================
# Property 8: Exception Classification Produces Type, Severity, and Resolution
# ===========================================================================


class TestExceptionClassificationCompleteness:
    """**Validates: Requirements 5.1, 5.2**

    For any exception payload handed off to the Intelligence Agent, the response
    SHALL include an exception type classification, a severity level, and a
    resolution recommendation.
    """

    @given(
        exception_type=exception_type_strategy,
        severity=severity_strategy,
        confidence=confidence_strategy,
    )
    @settings(max_examples=50)
    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_exception_response_always_contains_type_severity_resolution(
        self, mock_slm, mock_llm, exception_type, severity, confidence
    ):
        """Intelligence Agent response always includes exception_type, severity, and resolution_recommendation."""
        mock_slm.return_value = {
            "exception_type": exception_type,
            "severity": severity,
            "resolution_recommendation": "Take corrective action for the identified exception.",
            "confidence": confidence,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 130,
        }
        mock_llm.return_value = {
            "exception_type": exception_type,
            "severity": severity,
            "resolution_recommendation": "Perform detailed root-cause analysis and corrective action.",
            "confidence": 0.90,
            "model_tier": "LLM",
            "model_id": LLM_MODEL,
            "latency_ms": 500,
        }

        payload = {
            "shipment_id": "test-ship-prop8",
            "exception_type": exception_type,
            "severity_hint": severity,
        }

        result = invoke_intelligence_agent(payload)

        # exception_type must be present and non-empty
        assert "exception_type" in result
        assert isinstance(result["exception_type"], str)
        assert len(result["exception_type"]) > 0

        # severity must be present and non-empty
        assert "severity" in result
        assert isinstance(result["severity"], str)
        assert len(result["severity"]) > 0

        # resolution_recommendation must be present and non-empty
        assert "resolution_recommendation" in result
        assert isinstance(result["resolution_recommendation"], str)
        assert len(result["resolution_recommendation"]) > 0

    @given(
        exception_type=exception_type_strategy,
        severity=severity_strategy,
    )
    @settings(max_examples=50)
    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_exception_type_matches_input_classification(
        self, mock_slm, mock_llm, exception_type, severity
    ):
        """The classified exception_type in the response matches the input type."""
        mock_slm.return_value = {
            "exception_type": exception_type,
            "severity": severity,
            "resolution_recommendation": "Corrective action.",
            "confidence": 0.85,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 100,
        }

        payload = {
            "shipment_id": "test-ship-prop8-match",
            "exception_type": exception_type,
            "severity_hint": severity,
        }

        result = invoke_intelligence_agent(payload)

        assert result["exception_type"] == exception_type

    @given(
        exception_type=exception_type_strategy,
        severity=severity_strategy,
    )
    @settings(max_examples=50)
    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_severity_is_valid_level(
        self, mock_slm, mock_llm, exception_type, severity
    ):
        """The severity in the response is a valid level (low, medium, high)."""
        mock_slm.return_value = {
            "exception_type": exception_type,
            "severity": severity,
            "resolution_recommendation": "Take action.",
            "confidence": 0.80,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 100,
        }

        payload = {
            "shipment_id": "test-ship-prop8-sev",
            "exception_type": exception_type,
            "severity_hint": severity,
        }

        result = invoke_intelligence_agent(payload)

        assert result["severity"] in ("low", "medium", "high")
