"""Shared data models for the TMS MVP Demo."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Shipment:
    """Represents a shipment record in the TMS system.

    Stored in DynamoDB with PK=SHIPMENT#{shipment_id}, SK=STATUS.
    """

    shipment_id: str
    status: str  # created | planning | carrier_assigned | in_transit | exception | complete
    origin: str
    destination: str
    weight_kg: float
    commodity: str
    delivery_date: str  # ISO-8601
    created_at: str  # ISO-8601
    updated_at: str  # ISO-8601
    carrier_id: Optional[str] = None
    route: Optional[dict] = None  # {stops: [], distance_km, transit_hours}
    charges: Optional[dict] = None  # {base, fuel_surcharge, total, currency}


@dataclass
class AgentResponse:
    """Structured response from any AI agent (Orchestrator, Planning, Intelligence).

    Captures the result, confidence score, model tier used, and latency.
    """

    result: dict
    confidence: float  # 0.0 - 1.0
    model_tier: str  # "SLM" or "LLM"
    model_id: str  # e.g., "us.amazon.nova-lite-v1:0"
    latency_ms: int
    step_name: str


@dataclass
class TmsEvent:
    """Domain event published to EventBridge during the shipment lifecycle.

    Event types: ShipmentCreated, RouteOptimized, CarrierAssigned,
    ExceptionDetected, ExceptionResolved, ShipmentCompleted.
    """

    shipment_id: str
    timestamp: str  # ISO-8601
    step: str
    event_type: str
    payload: dict = field(default_factory=dict)
    model_tier: Optional[str] = None  # Only for AI steps
    confidence: Optional[float] = None  # Only for AI steps
