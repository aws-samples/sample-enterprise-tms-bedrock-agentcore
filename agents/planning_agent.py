"""Planning Agent — Route optimization with SLM-first, LLM fallback.

Uses the Strands Agents SDK on AgentCore Runtime.
SLM: Nova Lite (us.amazon.nova-lite-v1:0)
LLM: Claude Sonnet 4.6 (us.anthropic.claude-sonnet-4-20250514-v1:0)

The agent receives origin, destination, weight_kg, and shipment_id.
It returns an optimized route (stops, distance_km, transit_hours),
a confidence score, and which model tier produced the result.
"""

from __future__ import annotations

import json
import time
import logging
from typing import Any

from strands import Agent

from shared.constants import (
    CONFIDENCE_THRESHOLD,
    PLANNING_MODEL_SLM,
    LLM_MODEL,
    MEMORY_NAMESPACE_ROUTES,
)
from shared import memory

logger = logging.getLogger(__name__)

# --- System Prompt ---

PLANNING_SYSTEM_PROMPT = (
    "You are the Planning Agent for the TMS MVP demo. Your role is to optimize delivery routes "
    "given origin, destination, and shipment constraints. Use the SLM tier (Nova Lite) first. "
    "If your confidence in the route quality is below 0.75, escalate to the LLM (Claude Sonnet 4.6) "
    "for a more thorough optimization. Return the optimized route with stops, estimated distance, "
    "transit time, confidence score, and which model tier produced the result."
)

# --- Response Parsing ---


def _parse_route_response(raw_response: str) -> dict[str, Any]:
    """Parse the agent's raw text response into a structured route dict.

    The agent is instructed to return JSON with stops, distance_km, transit_hours,
    and confidence. This function extracts that JSON from the response text.
    """
    # Try to extract JSON from the response
    text = str(raw_response).strip()

    # Attempt direct JSON parse first
    try:
        parsed = json.loads(text)
        return _normalize_route(parsed)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to find JSON block in the response
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start:end])
            return _normalize_route(parsed)
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: return a default low-confidence response to trigger LLM escalation
    logger.warning("Could not parse route response, returning low confidence for escalation")
    return {
        "route": {"stops": [], "distance_km": 0.0, "transit_hours": 0.0},
        "confidence": 0.0,
    }


def _estimate_distance_km(payload: dict[str, Any]) -> float:
    """Coarse fallback distance (km) when the model didn't return a usable one.

    Uses a small table of known inter-city distances for the seeded lanes and a
    sensible default otherwise. This is a safety net so the deterministic
    downstream charge calculation always receives distance_km > 0; the AI agent
    still produces the real route whenever it returns valid output.
    """
    known = {
        ("Chicago, IL", "New York, NY"): 1270.0,
        ("Los Angeles, CA", "Dallas, TX"): 2000.0,
        ("Seattle, WA", "Denver, CO"): 2100.0,
        ("Atlanta, GA", "Miami, FL"): 1050.0,
        ("Boston, MA", "Philadelphia, PA"): 500.0,
    }
    key = (str(payload.get("origin", "")), str(payload.get("destination", "")))
    return known.get(key, 800.0)


def _normalize_route(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize parsed JSON into the expected route response shape."""
    route = parsed.get("route", {})
    if not isinstance(route, dict):
        route = {}

    stops = route.get("stops", parsed.get("stops", []))
    distance_km = float(route.get("distance_km", parsed.get("distance_km", 0.0)))
    transit_hours = float(route.get("transit_hours", parsed.get("transit_hours", 0.0)))
    confidence = float(parsed.get("confidence", 0.0))

    return {
        "route": {
            "stops": stops if isinstance(stops, list) else [],
            "distance_km": distance_km,
            "transit_hours": transit_hours,
        },
        "confidence": confidence,
    }


# --- Agent Invocation ---


def _build_user_prompt(payload: dict[str, Any], prior_context: str = "") -> str:
    """Build the user prompt for the planning agent from the request payload.

    ``prior_context`` (if any) injects relevant past route outcomes retrieved
    from AgentCore Memory so the agent can learn from prior decisions.
    """
    prior_block = f"\n{prior_context}\n" if prior_context else ""
    return (
        f"Optimize a delivery route with the following parameters:\n"
        f"- Origin: {payload['origin']}\n"
        f"- Destination: {payload['destination']}\n"
        f"- Weight: {payload['weight_kg']} kg\n"
        f"- Shipment ID: {payload['shipment_id']}\n"
        f"{prior_block}\n"
        f"Return your response as JSON with this exact structure:\n"
        f'{{"route": {{"stops": ["origin", "intermediate stop(s)", "destination"], '
        f'"distance_km": <number>, "transit_hours": <number>}}, '
        f'"confidence": <number between 0 and 1>}}\n\n'
        f"Include 1-2 realistic intermediate stops between origin and destination. "
        f"Estimate realistic distance in km and transit time in hours."
    )


def _invoke_agent(
    model_id: str, payload: dict[str, Any], prior_context: str = ""
) -> dict[str, Any]:
    """Invoke a Strands Agent with the given model and payload.

    Returns the parsed route response dict with route and confidence.
    """
    agent = Agent(
        system_prompt=PLANNING_SYSTEM_PROMPT,
        model=model_id,
    )

    user_prompt = _build_user_prompt(payload, prior_context)
    response = agent(user_prompt)
    raw_text = str(response)
    return _parse_route_response(raw_text)


def invoke_planning_agent(payload: dict[str, Any]) -> dict[str, Any]:
    """Orchestrate the SLM-first with LLM fallback flow for route optimization.

    Args:
        payload: Dict containing origin, destination, weight_kg, shipment_id.

    Returns:
        Dict with keys:
            - route: {stops: list, distance_km: float, transit_hours: float}
            - confidence: float (0.0 - 1.0)
            - model_tier: "SLM" or "LLM"
            - model_id: The model identifier used for the final result
            - latency_ms: Total invocation time in milliseconds
    """
    start_time = time.time()

    # Step 0: Retrieve relevant prior route outcomes from AgentCore Memory to
    # condition this decision (continuous-learning loop). Best-effort.
    prior_context = memory.summarize_prior(
        memory.retrieve_similar(
            query=f"route from {payload.get('origin')} to {payload.get('destination')}",
            namespace=MEMORY_NAMESPACE_ROUTES,
        )
    )

    def _finalize(result: dict, tier: str, model: str) -> dict:
        """Record the outcome to memory and build the response envelope.

        Guarantees a positive ``distance_km``: if the model didn't produce a
        usable distance, fall back to a coarse estimate so the deterministic
        downstream charge calculation (which requires distance > 0) never fails
        on a malformed model response.
        """
        route = result.get("route") or {}
        if float(route.get("distance_km", 0) or 0) <= 0:
            route["distance_km"] = _estimate_distance_km(payload)
            if float(route.get("transit_hours", 0) or 0) <= 0:
                # ~80 km/h average including stops.
                route["transit_hours"] = round(route["distance_km"] / 80.0, 1)
            result["route"] = route
        elapsed_ms = int((time.time() - start_time) * 1000)
        memory.record_outcome(
            actor_id="planning-agent",
            session_id=str(payload.get("shipment_id", "unknown")),
            outcome={
                "type": "route_optimized",
                "origin": payload.get("origin"),
                "destination": payload.get("destination"),
                "weight_kg": payload.get("weight_kg"),
                "route": result["route"],
                "confidence": result["confidence"],
                "model_tier": tier,
            },
            namespace=MEMORY_NAMESPACE_ROUTES,
        )
        return {
            "route": result["route"],
            "confidence": result["confidence"],
            "model_tier": tier,
            "model_id": model,
            "latency_ms": elapsed_ms,
        }

    # Step 1: Attempt with SLM (Nova Micro)
    try:
        slm_result = _invoke_agent(PLANNING_MODEL_SLM, payload, prior_context)
    except Exception as e:
        logger.warning("SLM invocation failed: %s. Falling through to LLM.", e)
        slm_result = {"route": {"stops": [], "distance_km": 0.0, "transit_hours": 0.0}, "confidence": 0.0}

    # Step 2: Check confidence threshold
    if slm_result["confidence"] >= CONFIDENCE_THRESHOLD:
        return _finalize(slm_result, "SLM", PLANNING_MODEL_SLM)

    # Step 3: Escalate to LLM (Claude Sonnet 4.6)
    logger.info(
        "SLM confidence %.2f below threshold %.2f — escalating to LLM",
        slm_result["confidence"],
        CONFIDENCE_THRESHOLD,
    )
    try:
        llm_result = _invoke_agent(LLM_MODEL, payload, prior_context)
    except Exception as e:
        logger.error("LLM invocation failed: %s", e)
        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "route": slm_result["route"],
            "confidence": slm_result["confidence"],
            "model_tier": "SLM",
            "model_id": PLANNING_MODEL_SLM,
            "latency_ms": elapsed_ms,
            "error": f"LLM fallback failed: {e}",
        }

    return _finalize(llm_result, "LLM", LLM_MODEL)
