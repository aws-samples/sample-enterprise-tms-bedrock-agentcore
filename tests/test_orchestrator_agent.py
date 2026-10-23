"""Unit tests for the Orchestrator Agent — SLM/LLM routing and Step Functions trigger."""

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.orchestrator_agent import (
    invoke_orchestrator_agent,
    start_shipment_workflow,
    _parse_classification_response,
    _create_agent,
    ORCHESTRATOR_SYSTEM_PROMPT,
)
from shared.constants import CONFIDENCE_THRESHOLD, ORCHESTRATOR_MODEL_SLM, LLM_MODEL


# --- Fixtures ---

VALID_PAYLOAD = {
    "origin": "Chicago, IL",
    "destination": "New York, NY",
    "weight_kg": 750.0,
    "commodity": "Electronics",
    "delivery_date": "2025-02-15",
}

MOCK_EXECUTION_ARN = (
    "arn:aws:states:us-east-1:123456789012:execution:tms-shipment-workflow:shipment-abc123"
)


class TestStartShipmentWorkflow:
    """Tests for the start_shipment_workflow tool."""

    @patch("agents.orchestrator_agent.boto3.client")
    def test_starts_execution_with_correct_params(self, mock_boto_client):
        mock_sfn = MagicMock()
        mock_boto_client.return_value = mock_sfn
        mock_sfn.start_execution.return_value = {
            "executionArn": MOCK_EXECUTION_ARN,
            "startDate": MagicMock(isoformat=lambda: "2025-01-15T10:00:00+00:00"),
        }

        with patch.dict(os.environ, {"STATE_MACHINE_ARN": "arn:aws:states:us-east-1:123:stateMachine:test"}):
            result = start_shipment_workflow(VALID_PAYLOAD)

        assert result["execution_arn"] == MOCK_EXECUTION_ARN
        mock_sfn.start_execution.assert_called_once()
        call_kwargs = mock_sfn.start_execution.call_args[1]
        assert call_kwargs["stateMachineArn"] == "arn:aws:states:us-east-1:123:stateMachine:test"
        assert json.loads(call_kwargs["input"]) == VALID_PAYLOAD

    @patch("agents.orchestrator_agent.boto3.client")
    def test_returns_error_when_arn_not_set(self, mock_boto_client):
        with patch.dict(os.environ, {}, clear=True):
            # Remove STATE_MACHINE_ARN if present
            os.environ.pop("STATE_MACHINE_ARN", None)
            result = start_shipment_workflow(VALID_PAYLOAD)

        assert result["execution_arn"] is None
        assert "error" in result
        mock_boto_client.assert_not_called()

    @patch("agents.orchestrator_agent.boto3.client")
    def test_returns_error_on_sfn_exception(self, mock_boto_client):
        mock_sfn = MagicMock()
        mock_boto_client.return_value = mock_sfn
        mock_sfn.start_execution.side_effect = Exception("Access denied")

        with patch.dict(os.environ, {"STATE_MACHINE_ARN": "arn:aws:states:us-east-1:123:stateMachine:test"}):
            result = start_shipment_workflow(VALID_PAYLOAD)

        assert result["execution_arn"] is None
        assert "Access denied" in result["error"]


class TestParseClassificationResponse:
    """Tests for _parse_classification_response."""

    def test_parses_valid_json_response(self):
        response = '{"classification": "transactional", "confidence": 0.92}'
        result = _parse_classification_response(response)
        assert result["classification"] == "transactional"
        assert result["confidence"] == 0.92

    def test_parses_json_embedded_in_text(self):
        response = 'Here is my analysis: {"classification": "complex", "confidence": 0.65} done.'
        result = _parse_classification_response(response)
        assert result["classification"] == "complex"
        assert result["confidence"] == 0.65

    def test_returns_default_on_invalid_response(self):
        response = "I cannot parse this request properly."
        result = _parse_classification_response(response)
        assert result["classification"] == "transactional"
        assert result["confidence"] == 0.80

    def test_handles_integer_confidence(self):
        response = '{"classification": "transactional", "confidence": 1}'
        result = _parse_classification_response(response)
        assert result["confidence"] == 1.0


class TestInvokeOrchestratorAgent:
    """Tests for invoke_orchestrator_agent — SLM/LLM routing logic."""

    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_uses_slm_when_confidence_above_threshold(self, mock_create_agent, mock_start_workflow):
        """When SLM confidence >= 0.75, should use SLM result without LLM fallback."""
        mock_agent = MagicMock()
        mock_agent.return_value = '{"classification": "transactional", "confidence": 0.85}'
        mock_create_agent.return_value = mock_agent
        mock_start_workflow.return_value = {"execution_arn": MOCK_EXECUTION_ARN}

        result = invoke_orchestrator_agent(VALID_PAYLOAD)

        assert result["model_tier"] == "SLM"
        assert result["model_id"] == ORCHESTRATOR_MODEL_SLM
        assert result["confidence"] == 0.85
        assert result["classification"] == "transactional"
        assert result["execution_arn"] == MOCK_EXECUTION_ARN
        # Only one agent created (SLM)
        mock_create_agent.assert_called_once_with(ORCHESTRATOR_MODEL_SLM)

    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_falls_back_to_llm_when_confidence_below_threshold(self, mock_create_agent, mock_start_workflow):
        """When SLM confidence < 0.75, should escalate to LLM."""
        slm_agent = MagicMock()
        slm_agent.return_value = '{"classification": "unknown", "confidence": 0.50}'

        llm_agent = MagicMock()
        llm_agent.return_value = '{"classification": "transactional", "confidence": 0.93}'

        mock_create_agent.side_effect = [slm_agent, llm_agent]
        mock_start_workflow.return_value = {"execution_arn": MOCK_EXECUTION_ARN}

        result = invoke_orchestrator_agent(VALID_PAYLOAD)

        assert result["model_tier"] == "LLM"
        assert result["model_id"] == LLM_MODEL
        assert result["confidence"] == 0.93
        assert result["classification"] == "transactional"
        assert result["execution_arn"] == MOCK_EXECUTION_ARN
        # Two agents created (SLM then LLM)
        assert mock_create_agent.call_count == 2
        mock_create_agent.assert_any_call(ORCHESTRATOR_MODEL_SLM)
        mock_create_agent.assert_any_call(LLM_MODEL)

    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_uses_slm_at_exact_threshold(self, mock_create_agent, mock_start_workflow):
        """When SLM confidence == 0.75 exactly, should use SLM (>= threshold)."""
        mock_agent = MagicMock()
        mock_agent.return_value = '{"classification": "transactional", "confidence": 0.75}'
        mock_create_agent.return_value = mock_agent
        mock_start_workflow.return_value = {"execution_arn": MOCK_EXECUTION_ARN}

        result = invoke_orchestrator_agent(VALID_PAYLOAD)

        assert result["model_tier"] == "SLM"
        assert result["confidence"] == 0.75
        mock_create_agent.assert_called_once_with(ORCHESTRATOR_MODEL_SLM)

    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_falls_back_to_llm_on_slm_exception(self, mock_create_agent, mock_start_workflow):
        """When SLM invocation throws, should fall back to LLM."""
        slm_agent = MagicMock()
        slm_agent.side_effect = Exception("Model timeout")

        llm_agent = MagicMock()
        llm_agent.return_value = '{"classification": "transactional", "confidence": 0.88}'

        mock_create_agent.side_effect = [slm_agent, llm_agent]
        mock_start_workflow.return_value = {"execution_arn": MOCK_EXECUTION_ARN}

        result = invoke_orchestrator_agent(VALID_PAYLOAD)

        assert result["model_tier"] == "LLM"
        assert result["confidence"] == 0.88
        assert result["execution_arn"] == MOCK_EXECUTION_ARN

    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_returns_error_when_both_models_fail(self, mock_create_agent, mock_start_workflow):
        """When both SLM and LLM fail, should return error with partial result."""
        slm_agent = MagicMock()
        slm_agent.side_effect = Exception("SLM timeout")

        llm_agent = MagicMock()
        llm_agent.side_effect = Exception("LLM timeout")

        mock_create_agent.side_effect = [slm_agent, llm_agent]

        result = invoke_orchestrator_agent(VALID_PAYLOAD)

        assert result["execution_arn"] is None
        assert "error" in result
        assert "LLM fallback failed" in result["error"]

    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_returns_execution_arn_from_workflow(self, mock_create_agent, mock_start_workflow):
        """Should include execution_arn from Step Functions in the response."""
        mock_agent = MagicMock()
        mock_agent.return_value = '{"classification": "transactional", "confidence": 0.90}'
        mock_create_agent.return_value = mock_agent
        mock_start_workflow.return_value = {"execution_arn": MOCK_EXECUTION_ARN}

        result = invoke_orchestrator_agent(VALID_PAYLOAD)

        assert result["execution_arn"] == MOCK_EXECUTION_ARN
        mock_start_workflow.assert_called_once_with(VALID_PAYLOAD)

    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_response_contains_all_required_fields(self, mock_create_agent, mock_start_workflow):
        """Response should contain classification, confidence, model_tier, execution_arn."""
        mock_agent = MagicMock()
        mock_agent.return_value = '{"classification": "transactional", "confidence": 0.82}'
        mock_create_agent.return_value = mock_agent
        mock_start_workflow.return_value = {"execution_arn": MOCK_EXECUTION_ARN}

        result = invoke_orchestrator_agent(VALID_PAYLOAD)

        assert "classification" in result
        assert "confidence" in result
        assert "model_tier" in result
        assert "model_id" in result
        assert "latency_ms" in result
        assert "execution_arn" in result

    @patch("agents.orchestrator_agent.start_shipment_workflow")
    @patch("agents.orchestrator_agent._create_agent")
    def test_latency_ms_is_non_negative(self, mock_create_agent, mock_start_workflow):
        """Latency should be a non-negative integer."""
        mock_agent = MagicMock()
        mock_agent.return_value = '{"classification": "transactional", "confidence": 0.90}'
        mock_create_agent.return_value = mock_agent
        mock_start_workflow.return_value = {"execution_arn": MOCK_EXECUTION_ARN}

        result = invoke_orchestrator_agent(VALID_PAYLOAD)

        assert isinstance(result["latency_ms"], int)
        assert result["latency_ms"] >= 0


class TestOrchestratorSystemPrompt:
    """Tests for system prompt configuration."""

    def test_system_prompt_mentions_nova_micro(self):
        assert "Nova Micro" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_system_prompt_mentions_confidence_threshold(self):
        assert "0.75" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_system_prompt_mentions_claude_sonnet(self):
        assert "Claude Sonnet" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_system_prompt_mentions_step_functions(self):
        assert "Step Functions" in ORCHESTRATOR_SYSTEM_PROMPT
