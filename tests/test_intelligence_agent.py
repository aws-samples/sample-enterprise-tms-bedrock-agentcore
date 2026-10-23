"""Unit tests for the Intelligence Agent.

Tests verify the SLM-first with LLM fallback routing logic,
response structure, and exception classification behavior.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.intelligence_agent import (
    INTELLIGENCE_PROMPT,
    _classify_exception,
    _extract_confidence,
    invoke_intelligence_agent,
)
from shared.constants import CONFIDENCE_THRESHOLD, INTELLIGENCE_MODEL_SLM, LLM_MODEL


class TestClassifyException:
    """Tests for the _classify_exception helper."""

    def test_classifies_delay_exception(self):
        """Correctly classifies a delay exception with resolution."""
        result = _classify_exception({
            "exception_type": "delay",
            "severity_hint": "medium",
        })
        assert result["exception_type"] == "delay"
        assert result["severity"] == "medium"
        assert "reroute" in result["resolution_recommendation"].lower()

    def test_classifies_damage_exception(self):
        """Correctly classifies a damage exception with resolution."""
        result = _classify_exception({
            "exception_type": "damage",
            "severity_hint": "high",
        })
        assert result["exception_type"] == "damage"
        assert result["severity"] == "high"
        assert "damage claim" in result["resolution_recommendation"].lower()

    def test_classifies_missing_exception(self):
        """Correctly classifies a missing shipment exception."""
        result = _classify_exception({
            "exception_type": "missing",
            "severity_hint": "high",
        })
        assert result["exception_type"] == "missing"
        assert result["severity"] == "high"
        assert "investigation" in result["resolution_recommendation"].lower()

    def test_unknown_exception_defaults_to_delay(self):
        """Unknown exception types fall back to delay resolution."""
        result = _classify_exception({
            "exception_type": "unknown_type",
            "severity_hint": "low",
        })
        assert result["exception_type"] == "unknown_type"
        assert result["severity"] == "low"
        # Falls back to delay resolution map
        assert "reroute" in result["resolution_recommendation"].lower()

    def test_uses_severity_default_when_hint_empty(self):
        """Uses the default severity for the exception type when hint is empty."""
        result = _classify_exception({
            "exception_type": "damage",
            "severity_hint": "",
        })
        assert result["severity"] == "high"

    def test_missing_fields_use_defaults(self):
        """Handles missing fields gracefully with defaults."""
        result = _classify_exception({})
        assert result["exception_type"] == "delay"
        assert result["severity"] == "medium"
        assert result["resolution_recommendation"] != ""


class TestExtractConfidence:
    """Tests for the _extract_confidence helper."""

    def test_extracts_confidence_from_json_response(self):
        """Parses confidence from a JSON-containing response."""
        mock_response = MagicMock()
        mock_response.__str__ = lambda self: '{"exception_type": "delay", "confidence": 0.87}'

        confidence = _extract_confidence(mock_response, {"exception_type": "delay"})
        assert confidence == 0.87

    def test_slm_fallback_confidence_for_delay(self):
        """Returns realistic SLM confidence for delay exception."""
        mock_response = MagicMock()
        mock_response.__str__ = lambda self: "Some text without JSON"

        confidence = _extract_confidence(mock_response, {"exception_type": "delay"})
        assert confidence == 0.82

    def test_slm_fallback_confidence_for_damage(self):
        """Returns realistic SLM confidence for damage (below threshold)."""
        mock_response = MagicMock()
        mock_response.__str__ = lambda self: "No parseable confidence"

        confidence = _extract_confidence(mock_response, {"exception_type": "damage"})
        assert confidence == 0.68
        assert confidence < CONFIDENCE_THRESHOLD

    def test_llm_confidence_is_higher(self):
        """LLM confidence values are above threshold."""
        mock_response = MagicMock()
        mock_response.__str__ = lambda self: "No parseable confidence"

        confidence = _extract_confidence(
            mock_response, {"exception_type": "delay"}, is_llm=True
        )
        assert confidence == 0.91
        assert confidence >= CONFIDENCE_THRESHOLD

    def test_invalid_confidence_value_uses_fallback(self):
        """Invalid confidence values (>1.0) trigger fallback."""
        mock_response = MagicMock()
        mock_response.__str__ = lambda self: '{"confidence": 1.5}'

        confidence = _extract_confidence(mock_response, {"exception_type": "delay"})
        # Should use fallback since 1.5 is not in [0, 1]
        assert 0.0 <= confidence <= 1.0


class TestInvokeIntelligenceAgentRouting:
    """Tests for the SLM-first with LLM fallback routing logic."""

    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_returns_slm_result_when_confidence_above_threshold(
        self, mock_slm, mock_llm
    ):
        """When SLM confidence >= 0.75, returns SLM result without calling LLM."""
        mock_slm.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "Reroute shipment.",
            "confidence": 0.82,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 150,
        }

        payload = {
            "shipment_id": "ship-001",
            "exception_type": "delay",
            "severity_hint": "medium",
        }

        result = invoke_intelligence_agent(payload)

        assert result["model_tier"] == "SLM"
        assert result["confidence"] == 0.82
        assert result["confidence"] >= CONFIDENCE_THRESHOLD
        mock_slm.assert_called_once_with(payload)
        mock_llm.assert_not_called()

    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_escalates_to_llm_when_confidence_below_threshold(
        self, mock_slm, mock_llm
    ):
        """When SLM confidence < 0.75, escalates to LLM."""
        mock_slm.return_value = {
            "exception_type": "damage",
            "severity": "high",
            "resolution_recommendation": "",
            "confidence": 0.68,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 120,
        }
        mock_llm.return_value = {
            "exception_type": "damage",
            "severity": "high",
            "resolution_recommendation": "Initiate damage claim with carrier.",
            "confidence": 0.91,
            "model_tier": "LLM",
            "model_id": LLM_MODEL,
            "latency_ms": 450,
        }

        payload = {
            "shipment_id": "ship-002",
            "exception_type": "damage",
            "severity_hint": "high",
        }

        result = invoke_intelligence_agent(payload)

        assert result["model_tier"] == "LLM"
        assert result["confidence"] == 0.91
        mock_slm.assert_called_once_with(payload)
        mock_llm.assert_called_once_with(payload)

    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_escalates_to_llm_when_slm_returns_zero_confidence(
        self, mock_slm, mock_llm
    ):
        """When SLM fails (confidence=0.0), escalates to LLM."""
        mock_slm.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "",
            "confidence": 0.0,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 50,
        }
        mock_llm.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "Reroute via alternate corridor.",
            "confidence": 0.89,
            "model_tier": "LLM",
            "model_id": LLM_MODEL,
            "latency_ms": 400,
        }

        payload = {
            "shipment_id": "ship-003",
            "exception_type": "delay",
            "severity_hint": "medium",
        }

        result = invoke_intelligence_agent(payload)

        assert result["model_tier"] == "LLM"
        mock_llm.assert_called_once()

    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_returns_slm_at_exact_threshold(self, mock_slm, mock_llm):
        """When SLM confidence == 0.75 exactly, returns SLM (threshold is inclusive)."""
        mock_slm.return_value = {
            "exception_type": "capacity",
            "severity": "low",
            "resolution_recommendation": "Identify alternate carriers.",
            "confidence": 0.75,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 130,
        }

        payload = {
            "shipment_id": "ship-004",
            "exception_type": "capacity",
            "severity_hint": "low",
        }

        result = invoke_intelligence_agent(payload)

        assert result["model_tier"] == "SLM"
        assert result["confidence"] == 0.75
        mock_llm.assert_not_called()


class TestInvokeIntelligenceAgentResponseStructure:
    """Tests for response structure validation."""

    @patch("agents.intelligence_agent._invoke_slm")
    def test_response_contains_all_required_fields(self, mock_slm):
        """Response includes exception_type, severity, resolution, confidence, model_tier."""
        mock_slm.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "Reroute shipment.",
            "confidence": 0.82,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 150,
        }

        result = invoke_intelligence_agent({
            "shipment_id": "ship-005",
            "exception_type": "delay",
            "severity_hint": "medium",
        })

        assert "exception_type" in result
        assert "severity" in result
        assert "resolution_recommendation" in result
        assert "confidence" in result
        assert "model_tier" in result
        assert "model_id" in result
        assert "latency_ms" in result

    @patch("agents.intelligence_agent._invoke_slm")
    def test_model_tier_is_slm_or_llm(self, mock_slm):
        """model_tier is always 'SLM' or 'LLM'."""
        mock_slm.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "Reroute.",
            "confidence": 0.85,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 100,
        }

        result = invoke_intelligence_agent({
            "shipment_id": "ship-006",
            "exception_type": "delay",
            "severity_hint": "medium",
        })

        assert result["model_tier"] in ("SLM", "LLM")

    @patch("agents.intelligence_agent._invoke_slm")
    def test_confidence_is_between_0_and_1(self, mock_slm):
        """Confidence score is always in [0.0, 1.0]."""
        mock_slm.return_value = {
            "exception_type": "delay",
            "severity": "medium",
            "resolution_recommendation": "Reroute.",
            "confidence": 0.82,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 100,
        }

        result = invoke_intelligence_agent({
            "shipment_id": "ship-007",
            "exception_type": "delay",
            "severity_hint": "medium",
        })

        assert 0.0 <= result["confidence"] <= 1.0

    @patch("agents.intelligence_agent._invoke_llm")
    @patch("agents.intelligence_agent._invoke_slm")
    def test_llm_response_contains_all_required_fields(self, mock_slm, mock_llm):
        """LLM fallback response also contains all required fields."""
        mock_slm.return_value = {
            "exception_type": "missing",
            "severity": "high",
            "resolution_recommendation": "",
            "confidence": 0.55,
            "model_tier": "SLM",
            "model_id": INTELLIGENCE_MODEL_SLM,
            "latency_ms": 100,
        }
        mock_llm.return_value = {
            "exception_type": "missing",
            "severity": "high",
            "resolution_recommendation": "Trigger carrier investigation.",
            "confidence": 0.88,
            "model_tier": "LLM",
            "model_id": LLM_MODEL,
            "latency_ms": 500,
        }

        result = invoke_intelligence_agent({
            "shipment_id": "ship-008",
            "exception_type": "missing",
            "severity_hint": "high",
        })

        assert result["exception_type"] == "missing"
        assert result["severity"] == "high"
        assert result["resolution_recommendation"] != ""
        assert result["model_tier"] == "LLM"
        assert result["model_id"] == LLM_MODEL


class TestIntelligenceAgentWithMockedStrands:
    """Tests using mocked Strands Agent to verify end-to-end behavior."""

    @patch("agents.intelligence_agent.Agent")
    def test_slm_agent_created_with_correct_model(self, mock_agent_class):
        """SLM agent is created with Nova Lite model ID."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = '{"confidence": 0.82}'
        mock_agent_instance.__str__ = lambda self: '{"confidence": 0.82}'
        mock_agent_class.return_value = mock_agent_instance

        from agents.intelligence_agent import _invoke_slm

        _invoke_slm({
            "shipment_id": "ship-009",
            "exception_type": "delay",
            "severity_hint": "medium",
        })

        mock_agent_class.assert_called_once_with(
            system_prompt=INTELLIGENCE_PROMPT,
            model=INTELLIGENCE_MODEL_SLM,
        )

    @patch("agents.intelligence_agent.Agent")
    def test_llm_agent_created_with_correct_model(self, mock_agent_class):
        """LLM agent is created with Claude Sonnet 4.6 model ID."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.return_value = '{"confidence": 0.91}'
        mock_agent_instance.__str__ = lambda self: '{"confidence": 0.91}'
        mock_agent_class.return_value = mock_agent_instance

        from agents.intelligence_agent import _invoke_llm

        _invoke_llm({
            "shipment_id": "ship-010",
            "exception_type": "damage",
            "severity_hint": "high",
        })

        mock_agent_class.assert_called_once_with(
            system_prompt=INTELLIGENCE_PROMPT,
            model=LLM_MODEL,
        )

    @patch("agents.intelligence_agent.Agent")
    def test_slm_failure_returns_zero_confidence(self, mock_agent_class):
        """When SLM raises an exception, returns confidence 0.0 for LLM fallback."""
        mock_agent_instance = MagicMock()
        mock_agent_instance.side_effect = RuntimeError("Model unavailable")
        mock_agent_class.return_value = mock_agent_instance

        from agents.intelligence_agent import _invoke_slm

        result = _invoke_slm({
            "shipment_id": "ship-011",
            "exception_type": "delay",
            "severity_hint": "medium",
        })

        assert result["confidence"] == 0.0
        assert result["model_tier"] == "SLM"

    @patch("agents.intelligence_agent.Agent")
    def test_full_flow_slm_to_llm_fallback(self, mock_agent_class):
        """End-to-end: SLM returns low confidence for damage, triggers LLM."""
        call_count = {"n": 0}

        def make_agent(*args, **kwargs):
            instance = MagicMock()
            call_count["n"] += 1
            if call_count["n"] == 1:
                # SLM call — return response without parseable confidence
                instance.return_value = "Analysis incomplete, low confidence"
                instance.__str__ = lambda self: "Analysis incomplete, low confidence"
            else:
                # LLM call — return high confidence
                instance.return_value = '{"confidence": 0.91, "exception_type": "damage"}'
                instance.__str__ = lambda self: '{"confidence": 0.91, "exception_type": "damage"}'
            return instance

        mock_agent_class.side_effect = make_agent

        result = invoke_intelligence_agent({
            "shipment_id": "ship-012",
            "exception_type": "damage",
            "severity_hint": "high",
        })

        # damage has SLM confidence 0.68 (below 0.75), so should escalate to LLM
        assert result["model_tier"] == "LLM"
        assert result["confidence"] >= CONFIDENCE_THRESHOLD
        assert mock_agent_class.call_count == 2
