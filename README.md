# Enterprise TMS on Amazon Bedrock AgentCore

A sample Transportation Management System (TMS) that runs a shipment from order intake through carrier assignment, tracking, exception handling, and freight charge calculation. The deterministic work runs on AWS Step Functions and AWS Lambda at zero AI cost. Only two lifecycle steps call an AI agent, and each agent tries a small language model (SLM) first and escalates to a large language model (LLM) only when confidence is low. It is aimed at 3PL and logistics teams getting started with agentic AI, and it is built so you can adopt individual pieces next to systems you already run.

> **Scope:** One `cdk deploy` provisions everything described in this README: the AI agents (running in Lambda with the Strands Agents SDK), the Step Functions backbone, a Next.js Transportation Cockpit (Amazon S3 + Amazon CloudFront, Amazon Cognito auth), Amazon API Gateway, Amazon Kinesis, Amazon S3 (documents + event archive), AWS AppSync (real-time UI), Amazon Bedrock AgentCore Memory, and AWS Glue / Amazon Athena over the event archive.
>
> **Optional / not provisioned:** Amazon QuickSight dashboards can be connected to the deployed Athena tables as a follow-on step; the stack does not create them.

![TMS Architecture Diagram](TMS%20Architecture%20Diagram.png)

![TMS Request Flow Diagram](TMS%20Request%20Flow%20Diagram.png)

---

## The Core Idea

Most TMS operations are transactional: given the same input they should always produce the same output, so they do not need a model. This sample puts that work on Step Functions + Lambda and reserves AI for the steps that need judgment.

| Path | What it does in this sample | AI cost |
|---|---|---|
| Step Functions + Lambda | Freight unit creation, carrier assignment, tracking update, exception detection, charge calculation, completion, event publishing | None |
| AI agents (Strands Agents SDK on Lambda) | Request classification (Orchestrator), route optimization (Planning), exception analysis (Intelligence) | SLM first, LLM only when needed |

Of the 8 states in the `tms-shipment-lifecycle` state machine, 6 are plain Lambda tasks with no model call. The other 2 hand off to an agent.

---

## Architecture: Components and Why They Were Chosen

Everything below is defined in a single CDK stack, `TmsMvpStack` ([cdk/tms_mvp_stack.py](cdk/tms_mvp_stack.py)).

### User-facing and ingress

| Component | What it does here | Why this choice |
|---|---|---|
| Next.js Transportation Cockpit on S3 + CloudFront | Static export built during `cdk deploy` (in a Docker build container), served from a private bucket through CloudFront (origin access control, HTTPS redirect). Reads `config.json` at startup for API URL, AppSync URL, and Cognito IDs. | Static hosting needs no servers. Runtime `config.json` avoids baking deploy-time values into the build. |
| Amazon Cognito user pool | Sign-in for the cockpit; authorizes both API Gateway and AppSync. Self-signup is disabled. | One identity source for every user-facing API. |
| Amazon API Gateway (REST, `POST /shipments`) | Cognito-authorized endpoint that invokes the Orchestrator Lambda. | Managed auth, throttling, and HTTPS in front of the workflow. |
| Orchestrator Lambda (`tms-invoke-orchestrator`) | Validates the request body, runs the Orchestrator agent to classify it, then starts the Step Functions execution. | Single ingress point that shows the SLM-first pattern on the cheapest task. |

### Deterministic backbone

| Component | What it does here | Why this choice |
|---|---|---|
| AWS Step Functions state machine `tms-shipment-lifecycle` | 8-state linear workflow (see [How a shipment flows](#how-a-shipment-flows)). Each state is a Lambda task with retries (2 attempts, backoff); agent states have a 60 s timeout; the execution times out after 5 minutes. | Visual, auditable, retryable orchestration with no AI cost. Every state has an explicit JSON contract. |
| Deterministic Lambda tools | `tms-create-freight-unit`, `tms-assign-carrier`, `tms-track-shipment`, `tms-detect-exception`, `tms-calculate-charges`, `tms-complete-shipment` | Each does one step: validate input, run fixed logic, write to DynamoDB, publish an event, return a structured result. |
| Carrier simulator Lambda (`tms-carrier-simulator`) | Returns carrier quotes for a lane. Uses seeded DynamoDB rates when the lane exists, otherwise generates simulated quotes. Invoked by AssignCarrier. | Stands in for real carrier rating/tendering APIs so the sample runs without external accounts. |
| Agent wrapper Lambdas | `tms-invoke-planning-agent` and `tms-invoke-intelligence-agent` call the agents, publish `RouteOptimized` / `ExceptionResolved`, and return a fixed shape to Step Functions. | Keeps agents behind the same Lambda task contract as every other step, so the workflow does not care whether a step is AI or not. |
| Lambda layer (Strands Agents SDK + pydantic) | Built at deploy time from [lambdas/agent_layer_requirements.txt](lambdas/agent_layer_requirements.txt); attached to the three agent Lambdas only. | Agent dependencies stay out of the deterministic functions. |

### Events, real-time, and streaming

| Component | What it does here | Why this choice |
|---|---|---|
| Amazon EventBridge custom bus `tms-mvp-events` | Every lifecycle Lambda publishes domain events with source `tms.mvp`. Three rules fan out all `tms.mvp` events. | Producers and consumers stay decoupled. New consumers attach with a rule and no producer changes. |
| Amazon SQS progress queue `tms-mvp-progress` (+ DLQ) | Receives every event; simple polling consumer. | Easy way to watch progress from a script or another system. |
| Event archiver Lambda (`tms-archive-event`) | Writes each event as JSON to the archive bucket at `events/dt=YYYY-MM-DD/<event_type>/...`. | Durable, partitioned history that Athena can query. |
| AWS AppSync GraphQL API + publisher Lambda (`tms-realtime-publisher`) | The publisher calls the `publishShipmentEvent` mutation (IAM/SigV4); the cockpit subscribes to `onShipmentEvent` (Cognito). Schema: [appsync/schema.graphql](appsync/schema.graphql). | Managed WebSocket subscriptions push live progress to the browser without a server to run. |
| Amazon Kinesis Data Streams `tms-mvp-telematics` + `tms-telematics-processor` Lambda | On-demand stream; the processor reads JSON tracking records and updates the shipment's tracking fields in DynamoDB. No model call. | Standard ingestion path for high-volume GPS/IoT updates. Note: this path is separate from the state machine's TrackShipment step, which simulates a location. |

### Data, memory, and analytics

| Component | What it does here | Why this choice |
|---|---|---|
| Amazon DynamoDB single table `tms-mvp-table` | Carriers (`CARRIER#`), rates (`RATE#origin#destination`), shipments (`SHIPMENT#`), shipment events, idempotency records. On-demand billing. | Low-latency key-value access for transactional state, no capacity planning. |
| Amazon S3 documents bucket | Provisioned for shipment documents (bills of lading, PODs, invoices). No Lambda in this sample writes to it yet. | Placeholder for document storage; an extension point. |
| Amazon S3 event archive bucket | Target of the archiver Lambda. | Cheap, durable store for every domain event. |
| AWS Glue database `tms_mvp`, table `domain_events` + Amazon Athena workgroup `tms-mvp` | SQL over the archived events (columns: `event_type`, `shipment_id`, `timestamp`, `step`, `model_tier`, `confidence`, `payload`, partition `dt`). | Ask questions like "how often did each agent escalate to the LLM" with no ETL. QuickSight or another BI tool can connect here (not provisioned). |
| Amazon Bedrock AgentCore Memory (`tms_mvp_memory`) | Stores route and exception outcomes; events expire after 90 days. Used by the Planning and Intelligence agents through [shared/memory.py](shared/memory.py). | Managed memory store so agents can look up prior outcomes. |
| Seed data custom resource (`tms-seed-data`) | On deploy, writes 5 carriers, 13 lane rates, and 1 sample shipment. | The sample works immediately after deploy. |

### Guardrails

[app.py](app.py) runs cdk-nag `AwsSolutionsChecks` on every synth. Findings that are deliberate for a deployable sample are suppressed with written justifications in [cdk/nag_suppressions.py](cdk/nag_suppressions.py). Review those before using any part of this in your own environment.

---

## How a Shipment Flows

1. **Ingress.** The cockpit (or any client with a Cognito token) calls `POST /shipments`. The Orchestrator Lambda validates the body, runs the Orchestrator agent (SLM first), and starts the `tms-shipment-lifecycle` execution. *Calls an agent.*
2. **CreateFreightUnit.** Creates the shipment and freight unit records in DynamoDB. Emits `ShipmentCreated`. *Zero AI cost.*
3. **OptimizeRoute.** Planning agent returns stops, `distance_km`, and `transit_hours`. Emits `RouteOptimized` with `model_tier` and `confidence`. *Calls an agent.*
4. **AssignCarrier.** Gets quotes from the carrier simulator and assigns the lowest `rate_usd`. Emits `CarrierAssigned`. *Zero AI cost.*
5. **TrackShipment.** Sets status `in_transit`, a simulated location about 60% along the lane, and an ETA 24 hours out. No event is published. *Zero AI cost.*
6. **DetectException.** Sets status `exception`. In this sample it always raises a `delay` with severity `medium` so the next step runs every time. Emits `ExceptionDetected`. *Zero AI cost.*
7. **AnalyzeException.** Intelligence agent returns `exception_type`, `severity`, `resolution_recommendation`, `confidence`, `model_tier`. Emits `ExceptionResolved`. *Calls an agent.*
8. **CalculateCharges.** Looks up the lane rate for the assigned carrier, adds a 15% fuel surcharge, and stores the breakdown on the shipment. Emits `ChargesCalculated`. *Zero AI cost.*
9. **CompleteShipment.** Sets status `complete`. Emits `ShipmentCompleted`. *Zero AI cost.*

Every event uses the same envelope, built in [shared/events.py](shared/events.py). The EventBridge `detail-type` equals `event_type`:

```json
{
  "event_type": "RouteOptimized",
  "shipment_id": "<shipment-id>",
  "timestamp": "2026-01-01T00:00:00+00:00",
  "step": "OptimizeRoute",
  "model_tier": "SLM",
  "confidence": 0.87,
  "payload": { "route": { "stops": [], "distance_km": 1270.0, "transit_hours": 15.9 } }
}
```

`model_tier` and `confidence` are `null` for deterministic steps. The event types are listed in `EVENT_TYPES` in [shared/constants.py](shared/constants.py).

---

## Where We Use an SLM vs. an LLM, and Why

Model IDs and the threshold live in [shared/constants.py](shared/constants.py).

| Agent | Role in this sample | SLM (tried first) | LLM fallback | Escalation trigger |
|---|---|---|---|---|
| Orchestrator ([agents/orchestrator_agent.py](agents/orchestrator_agent.py)) | Classifies the incoming shipment request, then starts the workflow | Amazon Nova Micro `us.amazon.nova-micro-v1:0` | Claude Sonnet 5 `us.anthropic.claude-sonnet-5` | SLM confidence < 0.75, or SLM call fails |
| Planning ([agents/planning_agent.py](agents/planning_agent.py)) | Point-to-point route with 1-2 intermediate stops, distance, transit time | Amazon Nova Micro `us.amazon.nova-micro-v1:0` | Claude Sonnet 5 `us.anthropic.claude-sonnet-5` | SLM confidence < 0.75, unparseable SLM output, or SLM call fails |
| Intelligence ([agents/intelligence_agent.py](agents/intelligence_agent.py)) | Classifies an exception, sets severity, recommends a resolution | Amazon Nova Lite `us.amazon.nova-lite-v1:0` | Claude Sonnet 5 `us.anthropic.claude-sonnet-5` | SLM confidence < 0.75, or SLM call fails |

`CONFIDENCE_THRESHOLD = 0.75`. The resulting tier and confidence are returned to Step Functions and published on every AI event, so you can see the split in the cockpit and query it in Athena.

**Why this split**

- **No model for deterministic work.** Creating records, picking the lowest quote, applying a surcharge, and changing status have one right answer. Code is faster, cheaper, and repeatable.
- **SLM for narrow, well-scoped tasks.** Classifying a request, a point-to-point route, or a standard exception type are bounded problems. A small model handles them at a fraction of the cost and latency.
- **LLM only when the SLM is unsure.** Low confidence is the signal that a case is unusual (novel exceptions, complex multi-constraint routing). Paying for a large model only then keeps average cost close to the SLM price.
- **Model-agnostic.** The models are examples. Swap any ID in `shared/constants.py`; the pattern stays the same.

**Notes**

- Claude models are invoked through cross-region inference profiles (the `us.` prefix). `MODELS_WITHOUT_TEMPERATURE` lists model families that reject the `temperature` parameter.
- `LLM_MODEL_HARDEST` (Claude Opus 5, `us.anthropic.claude-opus-5`) is defined as a hook for a third, hardest-case tier. No agent uses it in this sample.
- **AgentCore Memory.** Before deciding, the Planning and Intelligence agents retrieve similar prior outcomes (namespaces `tms/routes` and `tms/exceptions`); after deciding they record the outcome. The Planning agent adds retrieved outcomes to its prompt; the Intelligence agent retrieves and logs them but does not yet add them to its prompt. Memory is best-effort: if `MEMORY_ID` is unset or a call fails, the agents continue without it.

**What is simulated in this sample (so demos are repeatable)**

- **Intelligence agent:** it calls the model, but the returned type, severity, and resolution come from a fixed map (`_RESOLUTION_MAP`) keyed on exception type. Confidence is parsed from the model response when present; otherwise fixed per-type fallback values are used (`_extract_confidence`). Because DetectException always raises `delay`, the SLM fallback confidence (0.82) normally keeps this step on the SLM.
- **Planning agent:** if the model does not return a usable distance, `_estimate_distance_km` fills in a known distance for the seeded lanes (800 km otherwise) so CalculateCharges always gets a positive value.
- **Orchestrator agent:** the classification is returned and recorded, but the workflow is started for every valid request regardless of the classification.
- **Tracking, exceptions, and carrier quotes** are simulated (see the flow above).

---

## TMS Concepts in Plain English

| Term | What it means | Where it appears in this sample |
|---|---|---|
| Shipment | One movement of goods from an origin to a destination for a customer. | `SHIPMENT#<id>` record in DynamoDB; tracked through statuses `created` → `carrier_assigned` → `in_transit` → `exception` → `complete`. |
| Freight unit | The physical unit being moved (pallets, crates, a load) that gets planned and priced. Real TMSs may split one order into several. | CreateFreightUnit creates one freight unit per shipment (`freight_unit_id`). |
| Lane | A recurring origin-destination pair, such as Chicago, IL → New York, NY. Rates are usually quoted per lane. | 5 seeded lanes in [lambdas/seed_data/handler.py](lambdas/seed_data/handler.py). |
| Carrier | The company that physically moves the freight (trucking firm, parcel company). | 5 simulated carriers (Carrier A through Carrier E). |
| Rate / rate card | The agreed price a carrier charges on a lane, often by weight tier and service type. A rate card is the full table. | `RATE#origin#destination` records with `rate_usd`, `transit_days`, `weight_tier`, `service_type`. |
| Carrier quote | A carrier's price and transit time for a specific shipment. | Returned by the carrier simulator (seeded rate with a weight multiplier, or a generated rate for unknown lanes). |
| Tendering / carrier assignment | Offering a shipment to carriers and awarding it to one. | AssignCarrier awards the lowest-rate quote. There is no tender offer/accept round trip or auction in this sample. |
| Route optimization | Choosing the path and stops that meet constraints at the lowest cost or time. | Planning agent returns stops, distance, and transit time for a single origin-destination pair. |
| Transit time / ETA | How long the move takes, and the estimated time of arrival. | `transit_hours` from the Planning agent; `eta` from TrackShipment (simulated) or telematics records. |
| Telematics / tracking | Location and status data from trucks, devices, or carrier feeds. | Kinesis stream `tms-mvp-telematics` → telematics processor updates DynamoDB. |
| Shipment exception | Anything that goes off plan: a delay, damage, missing freight, a customs hold, or no capacity. | Intelligence agent handles `delay`, `damage`, `missing`, `customs_hold`, `capacity`. DetectException always raises `delay` in this sample. |
| Exception root-cause analysis | Working out why an exception happened and what to do about it. | AnalyzeException: Intelligence agent returns severity and a resolution recommendation (see simulation note above). |
| Freight charges | What the shipper owes for the move: a base charge plus surcharges (fuel, accessorials such as liftgate or detention). | CalculateCharges: `base_charge` (lane rate for the assigned carrier) + `fuel_surcharge` = `total_charge`, in USD. |
| Fuel surcharge | An extra charge that tracks fuel prices, usually a percentage of the base charge. | Fixed at 15% of the base charge. |
| Freight invoice | The carrier's bill for moving the shipment. It lists line items (base freight, fuel, accessorials), references the shipment and bill of lading, and is checked (freight audit) against the agreed rate and proof of delivery before payment. | Not generated here. CalculateCharges computes and stores the charge breakdown on the shipment and emits `ChargesCalculated`. Invoice generation, audit, and settlement are extension points. |
| Proof of delivery (POD) | Signed or scanned confirmation that the consignee received the goods. | Not implemented. CompleteShipment marks the shipment complete without a POD step. Extension point. |
| 3PL | Third-party logistics provider: a company that runs transportation (and often warehousing) for shippers. | The target user of this sample. |
| LTL / FTL / parcel | Less-than-truckload (shares a truck with other shippers' freight), full truckload (the whole truck), parcel (small packages). | Seeded service types include `ltl_freight`, `truckload`, `intermodal`, `ground_freight`, `express_freight`; carrier profiles include parcel and LTL offerings. |

---

## Three AI Agents: What Is Implemented vs. Designed For

| Agent | Implemented in this sample | Designed to extend to (not implemented) |
|---|---|---|
| Orchestrator | Classifies incoming shipment requests (SLM first) and starts the Step Functions workflow | Routing different request types to different workflows or agents; conflict resolution |
| Planning & Optimization | Point-to-point route with 1-2 stops, distance, and transit time; uses prior route outcomes from AgentCore Memory | Multi-stop optimization, 3D load planning, load consolidation, dynamic re-planning on disruptions |
| Intelligence | Exception classification, severity, and resolution recommendation (partly simulated, see above); records outcomes to AgentCore Memory | True root-cause analysis over shipment context, anomaly detection, natural-language queries over data, demand forecasting, contract analysis |

Deterministic capabilities that are also extension points: tender offer/accept and auctions, invoice generation and settlement, proof of delivery, SLA penalty calculation, and carrier performance score updates.

---

## Adopting It Modularly (Bring Your Own Components)

You do not need to adopt the whole stack. Each piece connects to the rest through a small, explicit contract.

### Integration seams

- **Event contract.** Bus `tms-mvp-events`, source `tms.mvp`, `detail-type` = one of `ShipmentCreated`, `RouteOptimized`, `CarrierAssigned`, `ExceptionDetected`, `ExceptionResolved`, `ChargesCalculated`, `ShipmentCompleted`, with the envelope from [shared/events.py](shared/events.py). Existing systems can publish these events, or subscribe with an EventBridge rule. That is exactly how the archiver, AppSync publisher, and SQS queue attach.
- **Workflow seam.** Every Step Functions state is a Lambda task with a fixed JSON output (see the `result_selector` fields in `_create_shipment_workflow`). You can point a state at your own Lambda or an adapter to an existing API, as long as it returns the same fields:

  | State | Must return |
  |---|---|
  | CreateFreightUnit | `shipment_id`, `freight_unit_id`, `status`, `created_at` |
  | OptimizeRoute | `shipment_id`, `route` (with `distance_km`), `confidence`, `model_tier` |
  | AssignCarrier | `carrier_id`, `carrier_name`, `rate_usd`, `service_type` |
  | TrackShipment | `current_status`, `location`, `eta` |
  | DetectException | `exception_detected`, `exception_type`, `severity_hint` |
  | AnalyzeException | `exception_type`, `severity`, `resolution_recommendation`, `confidence`, `model_tier` |
  | CalculateCharges | `base_charge`, `fuel_surcharge`, `total_charge`, `currency` |
  | CompleteShipment | `shipment_id`, `final_status`, `completed_at` |

  For example, AssignCarrier could call your rating/tendering engine instead of the carrier simulator, and CalculateCharges could call your freight-audit system.
- **Agent seam.** The Planning and Intelligence agents are reached through [lambdas/invoke_planning_agent](lambdas/invoke_planning_agent/handler.py) (input: `shipment_id`, `origin`, `destination`, `weight_kg`) and [lambdas/invoke_intelligence_agent](lambdas/invoke_intelligence_agent/handler.py) (input: `shipment_id`, `exception_type`, `severity_hint`). Call them from your own orchestration instead of this state machine, or change models in `shared/constants.py`.
- **Ingress seam.** `POST /shipments` with a Cognito token and a JSON body containing `origin`, `destination`, `weight_kg`, `commodity`, `delivery_date` (required fields in [lambdas/invoke_orchestrator/handler.py](lambdas/invoke_orchestrator/handler.py)). Or skip the Orchestrator and start the state machine directly with the same body (see `start-execution` below).
- **Telematics seam.** Put JSON records on the Kinesis stream `tms-mvp-telematics`: `shipment_id` (required, must exist), plus optional `status`, `location`, `eta` ([lambdas/telematics_processor/handler.py](lambdas/telematics_processor/handler.py)).
- **Analytics seam.** Query `tms_mvp.domain_events` in the Athena workgroup `tms-mvp`, or read the S3 archive directly. Connect QuickSight or your existing BI tool.
- **UI seam.** The cockpit only needs the API URL, AppSync URL, and Cognito IDs from `config.json`. To keep your own UI, subscribe to the AppSync `onShipmentEvent` subscription (optionally filtered by `shipment_id`).

### Common scenarios

| You already have | Use these parts of the sample | Integration point |
|---|---|---|
| A TMS or order management system | Planning and Intelligence agents, AgentCore Memory, event contract | Call the agent wrapper Lambdas from your workflow; publish/subscribe on the event bus |
| A carrier rating or tendering engine | Workflow, agents, events, cockpit | Replace AssignCarrier (and drop the carrier simulator) with an adapter that returns `carrier_id`, `carrier_name`, `rate_usd`, `service_type` |
| A freight audit / billing system | Everything up to CalculateCharges | Replace CalculateCharges, or subscribe to `ChargesCalculated` and build invoices downstream |
| A telematics or visibility platform | DynamoDB tracking state, events, cockpit | Forward updates to the Kinesis stream, or replace TrackShipment with a lookup against your platform |
| A data lake or BI stack | Event contract | Add an EventBridge rule to your own target, or read the S3 archive / Athena table |
| Your own operator UI | Backend only | Subscribe to `onShipmentEvent` and call `POST /shipments` |
| Only interest in the cost-optimized AI layer | `agents/`, `shared/constants.py`, `shared/memory.py`, the agent layer | Reuse the SLM-first/LLM-fallback pattern and memory helpers in your own Lambdas |

**How to take only some pieces today.** The CDK app is a single stack (`TmsMvpStack`) with no feature flags. To adopt part of it, copy or compose the relevant `_create_*` methods (for example `_create_event_bus`, `_create_shipment_workflow`, `_create_agentcore_memory`, `_create_realtime_api`) into your own stack and point the remaining seams at your systems. Keep the cdk-nag checks and review the suppressions as you go.

---

## Deploy It

The entire stack — agents, Step Functions workflow, API Gateway, Kinesis, S3,
AppSync, AgentCore Memory, Athena, and the Next.js cockpit on CloudFront — is
deployed with a single `cdk deploy`.

### Prerequisites

- **Python 3.12+** and **Node.js 20+**
- **Docker** running locally (CDK builds the Lambda layer and the Next.js
  cockpit inside build containers during `cdk deploy`)
- **AWS CLI v2** configured with credentials for your account/region
- **AWS CDK CLI**: `npm install -g aws-cdk`
- **Amazon Bedrock model access** (in `us-east-1`): enable Amazon Nova (Micro, Lite)
  and Anthropic Claude models in the Bedrock console under *Model access*

### Steps

```bash
# 1. Install Python dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Bootstrap CDK (first time per account/region only)
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1

# 3. Deploy the full stack (~15-20 min)
cdk deploy
```

When the deploy finishes, CDK prints outputs including `CockpitUrl` (the
CloudFront URL), `ApiUrl`, `UserPoolId`/`UserPoolClientId`, and
`ShipmentWorkflowArn`.

### Create a login and open the cockpit

Self-signup is disabled, so create a Cognito user (use your `UserPoolId` output):

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <UserPoolId> --username you@example.com \
  --temporary-password '<TEMPORARY_PASSWORD>' --message-action SUPPRESS

aws cognito-idp admin-set-user-password \
  --user-pool-id <UserPoolId> --username you@example.com \
  --password '<YOUR_PASSWORD>' --permanent
```

Replace `<TEMPORARY_PASSWORD>` and `<YOUR_PASSWORD>` with values that meet your
Cognito user pool's password policy. Do not commit real passwords.

Open the `CockpitUrl`, sign in, and click **Submit Shipment** to run the full
lifecycle with live progress (model tier + confidence shown for each AI step).

You can also trigger it without the UI:

```bash
aws stepfunctions start-execution \
  --state-machine-arn <ShipmentWorkflowArn> \
  --input '{"origin":"Chicago, IL","destination":"New York, NY","weight_kg":2500,"commodity":"Electronics","delivery_date":"2026-02-15"}'
```

### Clean up

```bash
cdk destroy
```

---

## Project Structure

```
├── agents/                        # 3 AI agents (Strands Agents SDK)
│   ├── orchestrator_agent.py      # Request classification (Nova Micro)
│   ├── planning_agent.py          # Route optimization (Nova Micro → Claude Sonnet 5)
│   └── intelligence_agent.py      # Exception analysis (Nova Lite → Claude Sonnet 5)
├── lambdas/                       # Lambda functions
│   ├── create_freight_unit/
│   ├── carrier_simulator/
│   ├── assign_carrier/
│   ├── track_shipment/
│   ├── detect_exception/
│   ├── calculate_charges/
│   ├── complete_shipment/
│   ├── invoke_planning_agent/     # Step Functions → Planning agent wrapper
│   ├── invoke_intelligence_agent/ # Step Functions → Intelligence agent wrapper
│   ├── invoke_orchestrator/       # API Gateway → Orchestrator agent
│   ├── archive_event/             # EventBridge → S3 event archive
│   ├── telematics_processor/      # Kinesis → DynamoDB tracking updates
│   ├── realtime_publisher/        # EventBridge → AppSync mutation
│   ├── seed_data/                 # CDK custom resource (carriers, rates)
│   └── agent_layer_requirements.txt  # Strands Agents Lambda layer dependencies
├── appsync/
│   └── schema.graphql             # AppSync GraphQL schema (real-time feed)
├── frontend/                      # Next.js Transportation Cockpit (S3 + CloudFront)
│   ├── app/                       # App Router pages
│   └── lib/                       # Config, Amplify (Cognito), API/subscription helpers
├── cdk/                           # Infrastructure as code
│   ├── tms_mvp_stack.py           # Full CDK stack
│   └── nag_suppressions.py        # cdk-nag suppressions with justifications
├── shared/                        # Shared utilities
│   ├── constants.py
│   ├── dynamo_utils.py
│   ├── events.py                  # Standardized domain-event publisher
│   └── memory.py                  # AgentCore Memory read/write helper
├── tests/                         # Unit tests
│   ├── property/                  # Property-based tests (Hypothesis)
│   └── smoke/                     # Post-deployment validation
├── app.py                         # CDK app entry point
├── cdk.json
├── pyproject.toml
└── requirements.txt
```

---

## Technology Stack

- **Runtime**: Python 3.12
- **Infrastructure**: AWS CDK (Python), single stack `TmsMvpStack`, cdk-nag AwsSolutionsChecks
- **Agents**: Strands Agents SDK running in AWS Lambda (packaged as a Lambda layer)
- **Amazon Bedrock AgentCore**: AgentCore Memory is provisioned and used. AgentCore Runtime, Gateway, Identity, Observability, and Policy are not provisioned in this sample; they are natural next steps for hosting agents, exposing tools over MCP, and enforcing Cedar policies.
- **Models**: Amazon Nova Micro / Nova Lite (SLM), Claude Sonnet 5 (LLM escalation); Claude Opus 5 defined but not wired
- **Workflows**: AWS Step Functions + AWS Lambda
- **Data and events**: Amazon DynamoDB, Amazon Kinesis Data Streams, Amazon S3, Amazon EventBridge, Amazon SQS
- **Real-time**: AWS AppSync (GraphQL subscriptions)
- **Analytics**: AWS Glue + Amazon Athena over the S3 event archive (QuickSight optional, not provisioned)
- **UI**: Next.js Transportation Cockpit (S3 + CloudFront, Cognito auth)
- **Carriers**: 5 simulated carriers (Carrier A through Carrier E)
- **Region**: us-east-1

---

## Documentation

- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [License](LICENSE)
