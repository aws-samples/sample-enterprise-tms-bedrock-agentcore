"""Shared constants for the TMS MVP Demo."""

# Infrastructure resource names
TABLE_NAME = "tms-mvp-table"
EVENT_BUS_NAME = "tms-mvp-events"

# SLM-first with LLM fallback threshold
CONFIDENCE_THRESHOLD = 0.75

# Model IDs — SLM tier
# Orchestrator uses Nova Micro (cheapest) for request classification/routing.
# Planning uses Nova Micro for point-to-point route calculation.
# Intelligence uses Nova Lite for exception classification / NL queries.
ORCHESTRATOR_MODEL_SLM = "us.amazon.nova-micro-v1:0"
PLANNING_MODEL_SLM = "us.amazon.nova-micro-v1:0"
INTELLIGENCE_MODEL_SLM = "us.amazon.nova-lite-v1:0"

# Model IDs — LLM tier (escalation).
# NOTE: Claude 5 models MUST be invoked via a cross-region inference profile
# (the "us." prefix); the bare "anthropic.claude-sonnet-5" ID rejects on-demand
# invocation. Claude 5 also DEPRECATES the `temperature` inference parameter —
# callers must omit `temperature` for these models (Nova still accepts it).
LLM_MODEL = "us.anthropic.claude-sonnet-5"

# Hardest-case escalation tier (novel exceptions, complex multi-constraint routes).
LLM_MODEL_HARDEST = "us.anthropic.claude-opus-5"

# Model families that reject the `temperature` inference parameter.
# Used by agent code to build per-tier inference config.
MODELS_WITHOUT_TEMPERATURE = ("claude-sonnet-5", "claude-opus-5")

# SQS queue for Streamlit progress polling
SQS_QUEUE_NAME = "tms-mvp-progress"

# DynamoDB key prefixes for single-table design
PK_CARRIER = "CARRIER#"
PK_RATE = "RATE#"
PK_SHIPMENT = "SHIPMENT#"
SK_PROFILE = "PROFILE"
SK_STATUS = "STATUS"
SK_EVENT_PREFIX = "EVENT#"

# EventBridge event source
EVENT_SOURCE = "tms.mvp"

# AgentCore Memory resource name — stores shipment/route/exception outcomes so
# agents can condition future decisions on prior outcomes (continuous learning).
AGENTCORE_MEMORY_NAME = "tms_mvp_memory"

# Namespaces within AgentCore Memory (logical partitions for actor/domain).
MEMORY_NAMESPACE_ROUTES = "tms/routes"
MEMORY_NAMESPACE_EXCEPTIONS = "tms/exceptions"

# Valid shipment statuses
SHIPMENT_STATUSES = [
    "created",
    "planning",
    "carrier_assigned",
    "in_transit",
    "exception",
    "complete",
]

# Valid event types.
# NOTE: "ChargesCalculated" is emitted by the CalculateCharges step; the final
# "ShipmentCompleted" is emitted only by the CompleteShipment step. These were
# previously conflated (both published "ShipmentCompleted"), which made the two
# distinct steps indistinguishable to consumers.
EVENT_TYPES = [
    "ShipmentCreated",
    "RouteOptimized",
    "CarrierAssigned",
    "ExceptionDetected",
    "ExceptionResolved",
    "ChargesCalculated",
    "ShipmentCompleted",
]
