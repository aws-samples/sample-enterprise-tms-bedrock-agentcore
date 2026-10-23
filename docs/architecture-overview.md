# Enterprise TMS — Architecture Overview (Consolidated)

> **Scope:** This architecture is deployed by the repository via `cdk deploy` — SLM-first agents + Step Functions backbone, a Next.js cockpit (S3 + CloudFront, Cognito auth), API Gateway, Kinesis, S3 (documents + event archive), AppSync (real-time UI), AgentCore Memory, and Athena/Glue. QuickSight dashboards are an optional documented follow-on (not provisioned). See [README-MVP.md](../README-MVP.md) for the deploy steps.

## What Is This System?

An enterprise Transportation Management System (TMS) built on Amazon Bedrock AgentCore. The system manages the full shipment lifecycle — order intake to delivery and invoicing — using a cost-optimized architecture: Step Functions + Lambda handle ~80-90% of operations deterministically (zero AI cost), while only 3 AI agents handle tasks that genuinely require reasoning. SLMs keep reasoning costs low; LLMs are reserved for complex problems.

---

## The Core Principle: AI Only Where It's Needed

Most TMS operations are transactional — create a freight unit, update a shipment status, distribute a tender, calculate charges from a rate card. These don't need AI. They need reliable, fast, deterministic execution.

The architecture splits cleanly:

| Path | What It Handles | Cost |
|---|---|---|
| Step Functions + Lambda | ~80-90% of operations: CRUD, validations, tender mechanics, charge calculations, status updates, event publishing | Zero AI cost |
| 3 AI Agents (AgentCore) | ~10-20% of operations: route optimization, exception analysis, NL queries, demand forecasting | SLM-first, LLM only when needed |

---

## The Three AI Agents

| Agent | What It Does | SLM Tasks | LLM Tasks |
|---|---|---|---|
| Orchestrator | Classifies incoming requests, routes to the right workflow or agent, resolves conflicts | Request classification, domain routing, priority assignment | Ambiguous request interpretation, multi-agent conflict resolution |
| Planning & Optimization | Route optimization, 3D load planning, consolidation, dynamic re-planning on disruptions | Single-stop routes, simple constraint validation | Multi-stop optimization (>5 stops), 3D load planning, re-planning |
| Intelligence | Exception root-cause analysis, anomaly detection, NL queries, demand forecasting, contract analysis | Exception classification, simple anomaly detection, basic NL parsing | Root-cause analysis, complex anomaly patterns, NL→SQL translation, forecasting |

All agents run on AgentCore Runtime. The Orchestrator uses Nova Micro (fastest/cheapest). Planning and Intelligence use Nova Lite for SLM tasks, Claude for LLM tasks.

---

## Step Functions — The Deterministic Backbone

Step Functions orchestrate the entire shipment lifecycle as a state machine. Each step calls Lambda tools directly — no agent, no tokens:

1. **Order Ingestion** → Lambda parses demand, classifies shipment type, creates freight units, validates against business rules
2. **Route Planning** → Step Functions hands off to Planning Agent (AI needed for optimization), gets back the optimized route, saves it
3. **Tendering** → Lambda distributes tenders to carriers, enforces deadlines, runs auction if enabled, assigns winning carrier
4. **Execution Monitoring** → Lambda consumes Kinesis telematics, updates shipment status. On exception → hands off to Intelligence Agent for analysis
5. **Costing** → Lambda applies rate cards, calculates charges, generates invoice, detects discrepancies
6. **Learning** → Lambda updates carrier performance scores, stores outcomes in AgentCore Memory

The key insight: Step Functions only hand off to an AI agent when reasoning is genuinely needed (route optimization, exception analysis). Everything else is deterministic.

---

## How a Request Flows

1. A request arrives at API Gateway
2. The Orchestrator Agent (SLM) classifies it:
   - Transactional (create order, check status, get rates) → routes directly to Step Functions
   - Needs reasoning (optimize route, analyze exception) → routes to the appropriate AI agent
3. For transactional requests: Step Functions executes the workflow using Lambda tools → DynamoDB → EventBridge → done
4. For reasoning requests: the AI agent follows MCP-first (deterministic tools via Gateway first), then invokes SLM/LLM only for the reasoning step
5. Results flow back through EventBridge, updating downstream workflows and the Transportation Cockpit

---

## The Three-Tier Cost Model

| Tier | When It's Used | Cost | Examples |
|---|---|---|---|
| Step Functions + Lambda | Transactional operations, CRUD, calculations | Zero AI cost | Create freight unit, update status, distribute tender, calculate charges, generate invoice |
| SLM (Nova Micro/Lite) | Simple reasoning, classification | Low | Request classification, exception severity, basic NL parsing, single-stop route |
| LLM (Claude/Nova Pro) | Complex multi-step reasoning | Higher | Multi-stop optimization, root-cause analysis, demand forecasting, NL→SQL |

---

## AgentCore Services We Use (6 of 9)

| Service | Role in TMS |
|---|---|
| Runtime | Runs 3 agents as serverless MicroVMs with auto-scaling |
| Gateway | Transforms Lambda functions into MCP tools, routes to SLM or LLM, enforces Cedar policies |
| Memory | Short-term (session context), Episodic (route outcomes, carrier history, user preferences), Semantic (rate cards, domain knowledge) |
| Identity | IAM-based RBAC — shippers, carriers, LSPs, and admins each see only their data |
| Observability | CloudWatch deep traces across agents, token usage and cost tracking per model tier |
| Policy | Cedar rules for agent boundaries and data access control |

Not used: Browser, Code Interpreter, Evaluations.

---

## What AgentCore Memory Stores

| Memory Tier | What's Stored | How It's Used |
|---|---|---|
| Short-term | Current session context, active task state | Maintains coherence within a single interaction |
| Episodic | Past route outcomes, carrier response times, exception resolutions, user preferences | Agents recall similar past situations for better decisions |
| Semantic | Rate cards, carrier profiles, vehicle constraints, regulatory rules, lane data | Domain knowledge referenced for every reasoning task |

---

## End-to-End Shipment Lifecycle

1. **Order received** → Step Functions: Lambda ingests demand, classifies type, creates freight units, validates
2. **Route planned** → AI handoff: Planning Agent optimizes route (SLM for simple, LLM for complex), stores pattern in memory
3. **Carrier selected** → Step Functions: Lambda ranks carriers, distributes tender, runs auction, assigns winner
4. **Shipment monitored** → Step Functions: Lambda consumes Kinesis telematics, updates status. On exception → AI handoff to Intelligence Agent
5. **Exceptions handled** → Intelligence Agent classifies severity (SLM), does root-cause analysis if needed (LLM), or escalates to HITL
6. **Charges calculated** → Step Functions: Lambda applies rate cards, generates invoice, flags discrepancies
7. **KPIs updated** → Lambda computes metrics, stores in S3 for Athena/QuickSight dashboards
8. **Learning loop** → Lambda updates carrier scores, stores outcomes in AgentCore Memory for future decisions

---

## What You Deploy (via CDK)

Since AgentCore manages the agent infrastructure, you provision:

- 3 AgentCore agents (Orchestrator, Planning, Intelligence)
- Step Functions workflows (shipment lifecycle, exception handling, tender/auction)
- Lambda functions — domain tools + external simulators
- DynamoDB — transactional state
- EventBridge — domain event routing
- Kinesis — real-time IoT/telematics streams
- S3 — event archive + analytics data lake
- QuickSight + Athena — KPI dashboards

---

## Why This Architecture Works for 3PL Customers

- **Low entry cost**: ~80-90% of operations run on Step Functions + Lambda with zero AI cost
- **Only 3 agents**: much less AgentCore Runtime cost than 7
- **SLM-first**: most reasoning tasks use cheap Nova Micro/Lite models
- **LLM only when needed**: Claude/Nova Pro reserved for genuinely complex problems
- **Incremental adoption**: customers can start with Step Functions only, add AI agents as they grow
- **Familiar AWS services**: Step Functions, Lambda, DynamoDB, EventBridge — services 3PL customers likely already use

---

## Key Design Decisions

- **Step Functions as the backbone**: deterministic workflows handle the bulk of operations — reliable, fast, zero AI cost
- **AI agents only for reasoning**: 3 agents instead of 7 — route optimization, exception analysis, and orchestration
- **SLM-first with LLM fallback**: attempt cheap model first, escalate only when confidence is low
- **Event-driven**: agents and workflows communicate via EventBridge — loosely coupled and scalable
- **AgentCore Gateway as control plane**: tool access, model routing, and policy enforcement in one place
- **3PL/Broker model**: works with external carriers (UPS, FedEx, DHL, XPO, J.B. Hunt, Schneider)

---

## Documentation

- [Architecture Diagrams](architecture-diagrams.md) — Mermaid diagrams for system architecture, data flow, agent scope, and deployment
- [Architecture Diagrams Explained](architecture-diagrams-explained.md) — Plain-English walkthrough of each diagram
