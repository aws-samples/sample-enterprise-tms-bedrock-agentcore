# TMS Demo — AgentCore Architecture

A deployable demonstration of the cost-optimized TMS architecture on Amazon Bedrock AgentCore. Drives a shipment through its full lifecycle using 3 AI agents, a Step Functions deterministic backbone, Lambda tools, EventBridge domain events, and a Next.js Transportation Cockpit — all provisioned by a single `cdk deploy`.

> **What deploys:** DynamoDB, Lambda tools, Step Functions workflow, EventBridge bus, Amazon Kinesis (telematics), Amazon S3 (documents + event archive), API Gateway (Cognito-authorized), AWS AppSync (real-time UI feed), Amazon Bedrock AgentCore Memory (continuous learning), Athena/Glue over the event archive, and a Next.js cockpit hosted on S3 + CloudFront.
>
> **Optional / not provisioned:** Amazon QuickSight dashboards can be connected to the Athena tables as a documented follow-on step; they are not created by the stack.

> **Deploy time**: ~15–20 minutes via `cdk deploy` (includes building and uploading the Next.js cockpit)

---

## What This Demonstrates

| Concept | How It's Shown |
|---|---|
| Step Functions as deterministic backbone | Most lifecycle steps execute at zero AI cost |
| SLM-first with LLM fallback | Planning + Intelligence agents try an SLM (Nova Micro/Lite) first, escalate to Claude Sonnet 5 when confidence < 0.75 |
| AI only where reasoning is needed | Only route optimization and exception analysis use AI |
| Event-driven architecture | Domain events published to EventBridge → archived to S3 and streamed to the UI via AppSync |
| Continuous learning | Agents record outcomes to AgentCore Memory and retrieve prior outcomes to condition new decisions |
| AgentCore Runtime | 3 agents built with the Strands SDK |

---

## Architecture

![TMS Architecture Diagram](TMS%20Architecture%20Diagram.png)

## Request Flow

![TMS Request Flow Diagram](TMS%20Request%20Flow%20Diagram.png)

---

## Quick Start

### Prerequisites

- **Python 3.12+**
- **Node.js 20+ and npm**
- **Docker** running locally (CDK builds the Lambda layer and the Next.js
  cockpit inside build containers during `cdk deploy`)
- **AWS CLI v2**, configured with credentials for your target account/region
- **AWS CDK CLI**: `npm install -g aws-cdk`
- **Amazon Bedrock model access**: in the target region (default `us-east-1`),
  enable access to the Amazon Nova (Micro, Lite) and Anthropic Claude models in
  the Bedrock console under *Model access*. The agents invoke these via
  cross-region inference profiles (`us.amazon.nova-*`, `us.anthropic.claude-*`).

### 1. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Bootstrap CDK (first time per account/region only)

```bash
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1
```

### 3. Deploy

```bash
cdk deploy
```

This provisions the full stack (see "What deploys" above) in ~15–20 minutes.
The Next.js cockpit is built and uploaded during the deploy, and its runtime
configuration (API Gateway, AppSync, and Cognito values) is written to the
hosting bucket as `config.json` automatically — nothing to wire by hand.

When it finishes, CDK prints outputs including:

- `CockpitUrl` — the CloudFront URL of the Transportation Cockpit
- `ApiUrl` — the API Gateway base URL
- `UserPoolId` / `UserPoolClientId` — Cognito identifiers
- `ShipmentWorkflowArn` — the Step Functions state machine ARN

### 4. Create a Cognito user

Self-signup is disabled, so create a user for the cockpit (replace the pool id
with your `UserPoolId` output):

```bash
aws cognito-idp admin-create-user \
  --user-pool-id <UserPoolId> \
  --username you@example.com \
  --temporary-password '<TEMPORARY_PASSWORD>' \
  --message-action SUPPRESS

aws cognito-idp admin-set-user-password \
  --user-pool-id <UserPoolId> \
  --username you@example.com \
  --password '<YOUR_PASSWORD>' \
  --permanent
```

Replace the placeholders with values that satisfy your Cognito user pool's
password policy. Never commit real credentials.

### 5. Use the cockpit

1. Open the `CockpitUrl` from the deploy outputs.
2. Sign in with the Cognito user you created.
3. Click **Submit Shipment**. Watch the lifecycle stream in real time (via
   AppSync), with the model tier (SLM/LLM) and confidence shown for each AI step.

### (Optional) Trigger the lifecycle without the UI

You can also start the workflow directly against Step Functions:

```bash
aws stepfunctions start-execution \
  --state-machine-arn <ShipmentWorkflowArn> \
  --input '{"origin":"Chicago, IL","destination":"New York, NY","weight_kg":2500,"commodity":"Electronics","delivery_date":"2026-02-15"}'
```

### 6. Clean up

Everything is created with a `DESTROY` removal policy, so a single command
tears the whole stack down and stops ongoing charges (CloudFront, Kinesis, etc.):

```bash
cdk destroy
```

---

## Project Structure

```
├── agents/                    # 3 AI agents (Strands SDK)
│   ├── orchestrator_agent.py  # Request classification (Nova Micro)
│   ├── planning_agent.py      # Route optimization (Nova Micro → Claude Sonnet 5)
│   └── intelligence_agent.py  # Exception analysis (Nova Lite → Claude Sonnet 5)
├── lambdas/                   # Lambda functions
│   ├── create_freight_unit/
│   ├── carrier_simulator/
│   ├── assign_carrier/
│   ├── track_shipment/
│   ├── detect_exception/
│   ├── calculate_charges/
│   ├── complete_shipment/
│   ├── invoke_planning_agent/     # Step Functions → Agent wrapper
│   ├── invoke_intelligence_agent/
│   ├── invoke_orchestrator/       # API Gateway → Orchestrator agent
│   ├── archive_event/             # EventBridge → S3 event archive
│   ├── telematics_processor/      # Kinesis → DynamoDB tracking updates
│   ├── realtime_publisher/        # EventBridge → AppSync mutation
│   └── seed_data/                 # CDK Custom Resource
├── appsync/                   # AppSync GraphQL schema (real-time feed)
│   └── schema.graphql
├── frontend/                  # Next.js Transportation Cockpit (S3 + CloudFront)
│   ├── app/                   # App Router pages
│   └── lib/                   # config, Amplify (Cognito), API/subscription helpers
├── cdk/                       # Infrastructure as Code
│   └── tms_mvp_stack.py       # Full CDK stack
├── shared/                    # Shared utilities
│   ├── constants.py
│   ├── models.py
│   ├── dynamo_utils.py
│   ├── events.py              # Standardized domain-event publisher
│   └── memory.py              # AgentCore Memory read/write helper
├── tests/
│   ├── smoke/                 # Post-deployment validation
│   └── property/              # Property-based tests (Hypothesis)
├── app.py                     # CDK app entry point
└── cdk.json
```

---

## Testing

**Unit tests** (no AWS needed):
```bash
python -m pytest tests/ -v --ignore=tests/smoke
```

**Property-based tests** (no AWS needed):
```bash
python -m pytest tests/property/ -v
```

**Smoke tests** (requires deployed stack):
```bash
export STATE_MACHINE_ARN=<arn>
python -m pytest tests/smoke/ -v
```

---

## Key Numbers

- **3** AI agents on AgentCore Runtime (Orchestrator, Planning, Intelligence)
- **6** deterministic Lambda tools (zero AI cost) + agent-wrapper, ingress, and event-processing Lambdas
- **1** Step Functions workflow (8 states)
- **7** EventBridge domain event types (ShipmentCreated, RouteOptimized, CarrierAssigned, ExceptionDetected, ExceptionResolved, ChargesCalculated, ShipmentCompleted)
- **1** DynamoDB table (single-table design, 5 carriers, 13 rates seeded)
- **0.75** confidence threshold for SLM → LLM escalation (Nova → Claude Sonnet 5)

---

## Related Documentation

- [Full Enterprise TMS README](README.md) — Complete production architecture
- [Architecture Diagrams](docs/architecture-diagrams.md) — System diagrams
- [Architecture Overview](docs/architecture-overview.md) — Detailed explanation
