"""Unit tests for the Planning Agent — SLM-first with LLM fallback routing logic."""

import json
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.planning_agent import (
    invoke_planning_agent,
    _parse_route_response,
    _normalize_route,
    _build_user_prompt,
    PLANNING_SYSTEM_PROMPT,
)
from shared.constants import CONFIDENCE_THRESHOLD, PLANNING_MODEL_SLM, LLM_MODEL


# --- Fixtures ---

VALID_PAYLOAD = {
    "origin": "Chicago, IL",
    "destination": "New York, NY",
    "weight_kg": 750.0,
    "shipment_id": "ship-001",
}

HIGH_CONFIDENCE_RESPONSE = json.dumps(
    {
        "route": {
            "stops": ["Chicago, IL", "Indianapolis, IN", "New York, NY"],
            "distance_km": 1200.0,
            "transit_hours": 14.5,
        },
        "confidence": 0.85,
    }
)

LOW_CONFIDENCE_RESPONSE = json.dumps(
    {
        "route": {
            "stops": ["Chicago, IL", "New York, NY"],
            "distance_km": 1100.0,
            "transit_hours": 12.0,
        },
        "confidence": 0.60,
    }
)

LLM_RESPONSE = json.dumps(
    {
        "route": {
            "stops": ["Chicago, IL", "Columbus, OH", "Philadelphia, PA", "New York, NY"],
            "distance_km": 1250.0,
            "transit_hours": 15.0,
        },
        "confidence": 0.92,
    }
)


# --- Test: Response Parsing ---


class TestParseRouteResponse:
    """Tests for _parse_route_response helper."""

    def test_parses_valid_json(self):
        result = _parse_route_response(HIGH_CONFIDENCE_RESPONSE)
        assert result["confidence"] == 0.85
        assert result["route"]["distance_km"] == 1200.0
        assert result["route"]["transit_hours"] == 14.5
        assert len(result["route"]["stops"]) == 3

    def test_parses_json_embedded_in_text(self):
        text = f"Here is the optimized route:\n{HIGH_CONFIDENCE_RESPONSE}\nDone."
        result = _parse_route_response(text)
        assert result["confidence"] == 0.85
        assert len(result["route"]["stops"]) == 3

    def test_returns_low_confidence_on_unparseable(self):
        result = _parse_route_response("This is not valid JSON at all")
        assert result["confidence"] == 0.0
        assert result["route"]["stops"] == []
        assert result["route"]["distance_km"] == 0.0

    def test_handles_flat_structure(self):
        flat = json.dumps(
            {
                "stops": ["A", "B", "C"],
                "distance_km": 500.0,
                "transit_hours": 6.0,
                "confidence": 0.78,
            }
        )
        result = _parse_route_response(flat)
        assert result["confidence"] == 0.78
        assert result["route"]["stops"] == ["A", "B", "C"]

    def test_handles_empty_string(self):
        result = _parse_route_response("")
        assert result["confidence"] == 0.0


# --- Test: Normalize Route ---


class TestNormalizeRoute:
    """Tests for _normalize_route helper."""

    def test_normalizes_nested_route(self):
        data = {
            "route": {"stops": ["X", "Y"], "distance_km": 100, "transit_hours": 2},
            "confidence": 0.9,
        }
        result = _normalize_route(data)
        assert result["route"]["stops"] == ["X", "Y"]
        assert result["route"]["distance_km"] == 100.0
        assert result["confidence"] == 0.9

    def test_defaults_missing_confidence(self):
        data = {"route": {"stops": ["A"], "distance_km": 50, "transit_hours": 1}}
        result = _normalize_route(data)
        assert result["confidence"] == 0.0

    def test_handles_non_dict_route(self):
        data = {"route": "invalid", "confidence": 0.5}
        result = _normalize_route(data)
        assert result["route"]["stops"] == []
        assert result["route"]["distance_km"] == 0.0


# --- Test: Build User Prompt ---


class TestBuildUserPrompt:
    """Tests for _build_user_prompt."""

    def test_includes_all_payload_fields(self):
        prompt = _build_user_prompt(VALID_PAYLOAD)
        assert "Chicago, IL" in prompt
        assert "New York, NY" in prompt
        assert "750.0" in prompt or "750" in prompt
        assert "ship-001" in prompt

    def test_requests_json_response(self):
        prompt = _build_user_prompt(VALID_PAYLOAD)
        assert "JSON" in prompt
        assert "stops" in prompt
        assert "distance_km" in prompt
        assert "transit_hours" in prompt
        assert "confidence" in prompt


# --- Test: SLM-First with LLM Fallback Logic ---


class TestInvokePlanningAgent:
    """Tests for invoke_planning_agent — the SLM/LLM routing orchestration."""

    @patch("agents.planning_agent._invoke_agent")
    def test_returns_slm_result_when_confidence_above_threshold(self, mock_invoke):
        """When SLM confidence >= 0.75, return SLM result without LLM call."""
        mock_invoke.return_value = {
            "route": {
                "stops": ["Chicago, IL", "Indianapolis, IN", "New York, NY"],
                "distance_km": 1200.0,
                "transit_hours": 14.5,
            },
            "confidence": 0.85,
        }

        result = invoke_planning_agent(VALID_PAYLOAD)

        assert result["model_tier"] == "SLM"
        assert result["model_id"] == PLANNING_MODEL_SLM
        assert result["confidence"] == 0.85
        assert result["route"]["distance_km"] == 1200.0
        assert len(result["route"]["stops"]) == 3
        assert "latency_ms" in result

        # SLM called once, no LLM call. Third arg is the (empty) prior-context
        # string injected from AgentCore Memory (disabled/no-op in tests).
        mock_invoke.assert_called_once_with(PLANNING_MODEL_SLM, VALID_PAYLOAD, "")

    @patch("agents.planning_agent._invoke_agent")
    def test_returns_slm_at_exact_threshold(self, mock_invoke):
        """Confidence exactly at 0.75 should use SLM (>= threshold)."""
        mock_invoke.return_value = {
            "route": {"stops": ["A", "B"], "distance_km": 500.0, "transit_hours": 6.0},
            "confidence": 0.75,
        }

        result = invoke_planning_agent(VALID_PAYLOAD)

        assert result["model_tier"] == "SLM"
        assert result["confidence"] == 0.75
        mock_invoke.assert_called_once_with(PLANNING_MODEL_SLM, VALID_PAYLOAD, "")

    @patch("agents.planning_agent._invoke_agent")
    def test_escalates_to_llm_when_confidence_below_threshold(self, mock_invoke):
        """When SLM confidence < 0.75, escalate to LLM."""
        slm_response = {
            "route": {"stops": ["A", "B"], "distance_km": 800.0, "transit_hours": 10.0},
            "confidence": 0.60,
        }
        llm_response = {
            "route": {
                "stops": ["A", "C", "D", "B"],
                "distance_km": 900.0,
                "transit_hours": 11.0,
            },
            "confidence": 0.92,
        }
        mock_invoke.side_effect = [slm_response, llm_response]

        result = invoke_planning_agent(VALID_PAYLOAD)

        assert result["model_tier"] == "LLM"
        assert result["model_id"] == LLM_MODEL
        assert result["confidence"] == 0.92
        assert result["route"]["stops"] == ["A", "C", "D", "B"]
        assert "latency_ms" in result

        # Both models called (third arg = empty prior-context from Memory)
        assert mock_invoke.call_count == 2
        mock_invoke.assert_any_call(PLANNING_MODEL_SLM, VALID_PAYLOAD, "")
        mock_invoke.assert_any_call(LLM_MODEL, VALID_PAYLOAD, "")

    @patch("agents.planning_agent._invoke_agent")
    def test_falls_through_to_llm_when_slm_raises(self, mock_invoke):
        """If SLM invocation raises an exception, fall through to LLM."""
        llm_response = {
            "route": {"stops": ["A", "B"], "distance_km": 600.0, "transit_hours": 7.0},
            "confidence": 0.88,
        }
        mock_invoke.side_effect = [RuntimeError("SLM unavailable"), llm_response]

        result = invoke_planning_agent(VALID_PAYLOAD)

        assert result["model_tier"] == "LLM"
        assert result["model_id"] == LLM_MODEL
        assert result["confidence"] == 0.88

    @patch("agents.planning_agent._invoke_agent")
    def test_returns_slm_result_with_error_when_both_fail(self, mock_invoke):
        """If both SLM and LLM fail, return SLM fallback result with error."""
        mock_invoke.side_effect = [
            RuntimeError("SLM unavailable"),
            RuntimeError("LLM unavailable"),
        ]

        result = invoke_planning_agent(VALID_PAYLOAD)

        # Should still return a result (from the SLM fallback with 0.0 confidence)
        assert result["model_tier"] == "SLM"
        assert result["confidence"] == 0.0
        assert "error" in result
        assert "latency_ms" in result

    @patch("agents.planning_agent._invoke_agent")
    def test_response_always_contains_required_fields(self, mock_invoke):
        """Every response must have route, confidence, model_tier, model_id, latency_ms."""
        mock_invoke.return_value = {
            "route": {"stops": ["A", "B"], "distance_km": 300.0, "transit_hours": 4.0},
            "confidence": 0.80,
        }

        result = invoke_planning_agent(VALID_PAYLOAD)

        assert "route" in result
        assert "confidence" in result
        assert "model_tier" in result
        assert "model_id" in result
        assert "latency_ms" in result
        assert isinstance(result["route"], dict)
        assert "stops" in result["route"]
        assert "distance_km" in result["route"]
        assert "transit_hours" in result["route"]

    @patch("agents.planning_agent._invoke_agent")
    def test_latency_ms_is_non_negative(self, mock_invoke):
        """latency_ms should always be a non-negative integer."""
        mock_invoke.return_value = {
            "route": {"stops": ["A", "B"], "distance_km": 100.0, "transit_hours": 1.0},
            "confidence": 0.90,
        }

        result = invoke_planning_agent(VALID_PAYLOAD)

        assert isinstance(result["latency_ms"], int)
        assert result["latency_ms"] >= 0


# --- Test: System Prompt ---


class TestSystemPrompt:
    """Tests for the system prompt content."""

    def test_system_prompt_mentions_slm_first(self):
        assert "SLM" in PLANNING_SYSTEM_PROMPT
        assert "Nova Lite" in PLANNING_SYSTEM_PROMPT

    def test_system_prompt_mentions_llm_fallback(self):
        assert "LLM" in PLANNING_SYSTEM_PROMPT
        assert "Claude Sonnet" in PLANNING_SYSTEM_PROMPT

    def test_system_prompt_mentions_confidence_threshold(self):
        assert "0.75" in PLANNING_SYSTEM_PROMPT

    def test_system_prompt_mentions_route_optimization(self):
        assert "route" in PLANNING_SYSTEM_PROMPT.lower()
        assert "optimize" in PLANNING_SYSTEM_PROMPT.lower()
