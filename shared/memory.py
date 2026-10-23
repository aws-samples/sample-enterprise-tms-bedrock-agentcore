"""AgentCore Memory helper — the continuous-learning store for TMS agents.

Agents use this module to:
- **Write outcomes** after a decision (a resolved exception, an optimized route)
  so the system accumulates a history it can learn from.
- **Retrieve relevant prior outcomes** before a decision, to condition the
  current reasoning on what happened in similar past situations.

This backs the blog's continuous-learning loop. It is intentionally honest
about scope: it stores outcomes and surfaces prior ones to the agent prompt; it
does not by itself claim measured accuracy improvement.

Design:
- Uses the Amazon Bedrock AgentCore data plane (``bedrock-agentcore`` client):
  ``CreateEvent`` to write, ``RetrieveMemoryRecords`` to read.
- The Memory resource id comes from the ``MEMORY_ID`` env var (set by the CDK
  stack from the ``CfnMemory`` resource). If unset (e.g. local unit tests),
  the helpers become safe no-ops so agents keep working without AgentCore.
- All calls are best-effort: memory is an enhancement, never a hard dependency,
  so failures are logged and swallowed rather than breaking a shipment.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)

_client = None


def _memory_client():
    global _client
    if _client is None:
        _client = boto3.client("bedrock-agentcore")
    return _client


def memory_id() -> str:
    """Return the configured AgentCore Memory id, or "" if unconfigured."""
    return os.environ.get("MEMORY_ID", "")


def is_enabled() -> bool:
    """True when a Memory resource is configured (so agents can skip cleanly)."""
    return bool(memory_id())


def record_outcome(
    actor_id: str,
    session_id: str,
    outcome: dict,
    namespace: Optional[str] = None,
) -> None:
    """Persist a decision outcome to AgentCore Memory.

    Args:
        actor_id: Logical actor/domain partition (e.g. "planning-agent").
        session_id: Correlates events for one shipment/session (the shipment_id).
        outcome: JSON-serializable outcome record (route chosen, resolution, etc.).
        namespace: Optional logical namespace hint stored in metadata.

    Best-effort: never raises.
    """
    if not is_enabled():
        logger.debug("AgentCore Memory not configured; skipping record_outcome")
        return

    try:
        _memory_client().create_event(
            memoryId=memory_id(),
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"json": {"content": outcome}}],
            metadata={"namespace": namespace} if namespace else {},
        )
        logger.info("Recorded outcome to AgentCore Memory for actor=%s session=%s",
                    actor_id, session_id)
    except Exception as e:  # noqa: BLE001 — memory must never break a shipment
        logger.warning("Failed to record outcome to AgentCore Memory: %s", str(e))


def retrieve_similar(
    query: str,
    namespace: Optional[str] = None,
    top_k: int = 3,
) -> list[dict]:
    """Retrieve prior outcomes relevant to ``query`` to condition a decision.

    Args:
        query: Natural-language description of the current situation.
        namespace: Optional namespace to scope the search.
        top_k: Max number of prior records to return.

    Returns:
        A list of prior outcome record dicts (possibly empty). Never raises.
    """
    if not is_enabled():
        return []

    try:
        kwargs: dict[str, Any] = {
            "memoryId": memory_id(),
            "searchCriteria": {"searchQuery": query, "topK": top_k},
        }
        if namespace:
            kwargs["namespace"] = namespace
        resp = _memory_client().retrieve_memory_records(**kwargs)
        records = resp.get("memoryRecordSummaries", resp.get("memoryRecords", []))
        logger.info("Retrieved %d prior memory record(s) for query", len(records))
        return records
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to retrieve from AgentCore Memory: %s", str(e))
        return []


def summarize_prior(records: list[dict], limit: int = 3) -> str:
    """Render retrieved memory records into a compact prompt-injection string.

    Returns an empty string when there is nothing useful, so callers can append
    unconditionally.
    """
    if not records:
        return ""
    lines = []
    for rec in records[:limit]:
        # Records vary in shape across strategies; extract a best-effort summary.
        content = rec.get("content") or rec.get("memoryRecord", {}).get("content") or rec
        lines.append(f"- {str(content)[:300]}")
    if not lines:
        return ""
    return "Relevant prior outcomes:\n" + "\n".join(lines)
