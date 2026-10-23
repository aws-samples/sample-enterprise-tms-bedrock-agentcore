# Enterprise TMS — Architecture Diagrams Explained

> **Scope:** This walkthrough explains the architecture the repository deploys via `cdk deploy`, including the Next.js cockpit (S3 + CloudFront, Cognito auth), API Gateway, Kinesis, S3, AppSync (real-time UI), and AgentCore Memory. QuickSight dashboards are an optional documented follow-on over the deployed Athena tables (not provisioned). See [README-MVP.md](../README-MVP.md).

This document walks through each of the 8 architecture diagrams in plain English. Read this alongside [architecture-diagrams.md](architecture-diagrams.md) to understand how the system fits together.

---

## Diagram 1: High-Level System Architecture

**What it shows:** The entire system at a glance — every major component and how they connect.

**How to read it:**

At the top, you have four external clients — Shipper Portal, Carrier Portal, LSP Portal, and the Transportation Cockpit (a Next.js web UI for human operators). All of them talk to the system through API Gateway.

From API Gateway, requests can go two ways:

1. **To the Orchestrator Agent** — for requests that need classification or might need AI reasoning. The Orchestrator (running on Nova Micro, the cheapest SLM) looks at the request and decides: is this transactional or does it need intelligence? If transactional, it routes to Step Functions. If it needs reasoning, it routes to either the Planning Agent or the Intelligence Agent.

2. **Directly to Step Functions** — for requests that are clearly transactional (like "create an order" or "check shipment status"). No AI involved, no token cost.

The Step Functions backbone has three workflows:
- **Shipment Lifecycle** — the main workflow that takes a shipment from order to delivery
- **Exception Handling** — triggered when something goes wrong during execution
- **Tender & Auction** — manages the carrier selection process

These workflows call Lambda functions (the "domain tools") to do the actual work — creating freight units, tracking shipments, managing carriers, calculating costs, computing KPIs. These Lambda functions read/write to DynamoDB, consume Kinesis streams (for real-time GPS/IoT data), and store analytics in S3.

When Step Functions hits something that needs reasoning — like "find the optimal route for this multi-stop shipment" — it hands off to the Planning Agent. When it detects an exception it can't auto-resolve, it hands off to the Intelligence Agent.

The AI agents talk to AgentCore Gateway, which does two things:
- **Tool calls**: routes deterministic operations back to the same Lambda functions (zero token cost)
- **Model calls**: routes reasoning tasks to either SLMs (Nova Micro/Lite) or LLMs (Claude/Nova Pro) based on complexity

AgentCore Memory stores context across three tiers — short-term (current session), episodic (past outcomes like route performance and carrier history), and semantic (domain knowledge like rate cards and vehicle constraints).

EventBridge ties everything together as the event backbone — Step Functions, agents, and the Transportation Cockpit all publish and subscribe to domain events.

External simulators (Lambda functions) generate realistic data for carrier rates, GPS positions, warehouse operations, and IoT sensors — these simulate the external services a real 3PL would integrate with.

**Key takeaway:** Most of the system is the green Step Functions + Lambda path. The AI agents (red) only get involved for the ~10-20% of operations that genuinely need reasoning.

---

## Diagram 2: Deterministic vs AI Split

**What it shows:** A decision tree that makes the split explicit — which operations go through Step Functions (zero AI cost) and which go through AI agents.

**How to read it:**

Every incoming TMS request hits a classification point. The question is simple: is this transactional/CRUD, or does it need reasoning?

**Step Functions path (~80-90% of operations):**
- Create or update freight units
- Shipment status updates
- Distribute tenders to carriers
- Enforce tender deadlines
- Assign a carrier to a shipment
- Calculate charges from rate cards
- Generate invoices
- Settlement summaries
- Compute KPIs from raw data
- Publish events to EventBridge
- Audit logging
- Validate business rules

None of these need an AI model. They're deterministic — given the same input, you always get the same output. Step Functions + Lambda handles them reliably and cheaply.

**AI Agent path (~10-20% of operations):**
- Multi-stop route optimization (too many variables for a simple algorithm)
- 3D load planning (spatial reasoning)
- Dynamic re-planning when disruptions happen (reasoning about alternatives)
- Exception root-cause analysis (pattern recognition across multiple signals)
- Anomaly pattern detection (statistical reasoning)
- Natural language queries ("show me all late shipments from last week")
- Demand forecasting (predictive reasoning)
- Complex carrier analysis (multi-factor evaluation)
- Load consolidation optimization (combinatorial reasoning)
- Ambiguous request classification (understanding intent)

These genuinely benefit from language model reasoning — they involve multiple constraints, ambiguity, or pattern recognition that rule-based logic can't handle well.

**Key takeaway:** The architecture is deliberately designed so that the expensive AI path is the exception, not the rule. A 3PL customer processing 1,000 shipments/day might only need AI for 100-200 of those operations.

---

## Diagram 3: The Three AI Agents — Scope and Model Usage

**What it shows:** What each of the 3 agents does, and which tasks use SLMs (cheap) vs LLMs (expensive).

**How to read it:**

Each agent has two tiers of model usage:

**Orchestrator Agent:**
- SLM (Nova Micro — fastest, cheapest): Request classification, domain routing, priority assignment. These are quick decisions — "this is an order creation request, route to Step Functions" or "this needs route optimization, send to Planning Agent."
- LLM (Claude): Only for ambiguous requests where the SLM can't confidently classify, or when multiple agents disagree and need conflict resolution.

**Planning & Optimization Agent:**
- SLM (Nova Lite): Single-stop route queries, simple constraint validation. If someone asks "what's the best route from warehouse A to customer B?" — that's straightforward enough for an SLM.
- LLM (Claude): Multi-stop route optimization (>5 stops), 3D load planning, dynamic re-planning when disruptions occur, consolidation analysis. These involve complex multi-constraint reasoning.

**Intelligence Agent:**
- SLM (Nova Lite): Exception classification (is this a delay, damage, or customs hold?), simple anomaly detection, basic natural language query parsing.
- LLM (Claude): Exception root-cause analysis (why did this happen?), complex anomaly patterns across multiple shipments, translating natural language to SQL queries, demand forecasting, contract analysis.

**Key takeaway:** The SLM handles the high-frequency, simpler reasoning tasks. The LLM only kicks in when the problem is genuinely complex. This keeps costs low — most AI invocations use the cheap model tier.

---

## Diagram 4: Shipment Lifecycle — Step Functions Backbone with AI Handoff

**What it shows:** The complete journey of a shipment from order to delivery, showing exactly when Step Functions handles things deterministically and when it hands off to an AI agent.

**How to read it as a story:**

**Step 1 — Order Ingestion (Deterministic):**
A client submits a transportation demand through API Gateway. Step Functions kicks off the shipment lifecycle workflow. Lambda parses the demand, classifies the shipment type, creates freight units in DynamoDB, and validates everything against business rules. If validation fails, Lambda tries rule-based auto-correction. If that doesn't work, it escalates to the Transportation Cockpit for a human to review. Once freight units are created, a `freight-unit.created` event is published.

No AI involved. Pure Lambda execution.

**Step 2 — Route Planning (AI Handoff):**
This is where Step Functions hands off to the Planning Agent — because route optimization genuinely needs reasoning. The Planning Agent first checks AgentCore Memory for similar past routes (episodic memory). It calls the Gateway to fetch freight unit data via MCP tools (deterministic, zero token cost). Then it asks the Gateway for route optimization — the Gateway classifies complexity: if ≤5 stops, it uses Nova Lite (SLM); if >5 stops, it uses Claude (LLM). The optimized route comes back, gets stored in episodic memory for future reference, and Step Functions saves it to DynamoDB. A `route.optimized` event is published.

AI is used here, but only for the reasoning step. Data retrieval is still deterministic via MCP tools.

**Step 3 — Tendering (Deterministic):**
Back to Step Functions. Lambda fetches carrier rates, ranks carriers based on price/performance/availability, creates a tender, and distributes it to the top-ranked carriers. Step Functions waits for responses with a timeout. If carriers respond, Lambda selects the winner and assigns them. If no one responds, it escalates to the Transportation Cockpit for manual assignment. A `carrier.assigned` event is published.

No AI. Carrier ranking uses a scoring algorithm, not a language model.

**Step 4 — Execution Monitoring (Deterministic + AI):**
Step Functions monitors the shipment via Lambda consuming Kinesis telematics data (GPS, IoT sensors). Lambda updates shipment status in DynamoDB and publishes `shipment.status-changed` events. This is all deterministic.

But if an exception is detected (delay, damage, customs hold, etc.), Step Functions hands off to the Intelligence Agent. The agent classifies the exception — if it's straightforward (SLM can handle it), it auto-resolves. If it's complex, the LLM does root-cause analysis and either resolves it or escalates to a human operator.

**Step 5 — Costing (Deterministic):**
Lambda fetches the rate card, calculates charges based on the actual shipment (weight, distance, surcharges), generates an invoice, and persists it to DynamoDB. A `invoice.generated` event is published.

Pure math. No AI needed.

**Step 6 — Learning (Deterministic + Memory):**
Lambda updates the carrier's performance score based on this shipment's outcome (on-time? damage-free? responsive?). It also stores the outcome in AgentCore Memory — episodic memory captures carrier performance and route variance so future AI decisions are informed by real history.

**Key takeaway:** Out of 6 lifecycle steps, only 2 involve AI (route planning and exception handling). The other 4 are fully deterministic. And even in the AI steps, data retrieval is done via MCP tools (zero token cost) — the model is only invoked for the actual reasoning.

---

## Diagram 5: SLM/LLM Decision Flow

**What it shows:** How the AgentCore Gateway decides whether to use a tool (free), an SLM (cheap), or an LLM (expensive) for any given request.

**How to read it:**

When a request arrives at an AI agent, the first question is: can this be handled by an MCP tool? If yes — meaning it's a deterministic operation like fetching data, running a calculation, or calling an external service — the Gateway invokes the Lambda tool directly. Zero token cost. Before execution, Cedar policy checks ensure the caller has permission.

If the request genuinely needs reasoning (not a tool call), the Gateway classifies complexity:

- **Low complexity** → SLM (Nova Micro or Lite). Fast, cheap. The SLM processes the request and checks its own confidence. If confidence is ≥ 0.75, the response is returned directly.
- **Low confidence from SLM** → automatic escalation to LLM (Claude or Nova Pro). The SLM tried but wasn't confident enough, so the LLM takes over.
- **High complexity** → LLM directly. No point wasting an SLM call on something that clearly needs heavy reasoning.

After any model response, the result is stored in AgentCore Memory for future reference.

**Key takeaway:** This is the SLM-first-with-LLM-fallback pattern. The system always tries the cheapest option first. LLMs are the last resort, not the default. For a typical workload, the vast majority of model invocations will be SLM-tier.

---

## Diagram 6: AWS Deployment Architecture

**What it shows:** Every AWS service you actually deploy, organized by category.

**How to read it:**

**AgentCore (fully managed — you configure, AWS runs it):**
- 3 agents in AgentCore Runtime (Orchestrator, Planning, Intelligence)
- AgentCore Gateway for MCP tool routing, model routing, and Cedar policy enforcement
- AgentCore Memory for the three memory tiers
- Identity, Observability, and Policy services

**Amazon Bedrock Models:**
- Nova Micro/Lite as the SLM tier
- Claude/Nova Pro as the LLM tier

**Step Functions (you define the workflows):**
- Shipment Lifecycle Workflow
- Exception Handling Workflow
- Tender & Auction Workflow

**Lambda Functions — Domain Tools (you write these):**
- Order & Freight Unit
- Shipment Tracking
- Carrier & Tendering
- Costing & Invoice
- KPI & Metrics

**Lambda Functions — Simulators (you write these):**
- Carrier Rates (simulates UPS, FedEx, DHL, XPO, J.B. Hunt, Schneider APIs)
- GPS/Telematics (simulates real-time vehicle tracking)
- Warehouse (simulates warehouse management systems)
- IoT Sensors (simulates temperature, humidity, shock sensors)

**Event & Data:**
- EventBridge for domain event routing
- DynamoDB for transactional state
- Kinesis for real-time IoT/telematics streams
- S3 for event archive and analytics data lake

**Analytics & UI:**
- QuickSight + Athena for KPI dashboards
- Transportation Cockpit (Next.js) for human operators

**Infrastructure as Code:**
- AWS CDK (Python) provisions everything

**Key takeaway:** AgentCore manages the agent infrastructure. You provision the workflows, tools, data stores, and UI. The CDK stacks handle all of it as code.

---

## Diagram 7: Event-Driven Flow

**What it shows:** How domain events flow through EventBridge to coordinate Step Functions, AI agents, and the Transportation Cockpit.

**How to read it as a timeline:**

1. Step Functions tells Lambda to ingest an order and create freight units. Lambda publishes `freight-unit.created` to EventBridge.

2. EventBridge triggers the Planning Agent. The agent queries episodic memory for similar past routes, optimizes the route, publishes `route.optimized`, and stores the outcome in memory.

3. EventBridge signals Step Functions to continue to the tendering phase. Lambda distributes the tender, waits for carrier responses, and publishes `carrier.assigned`.

4. EventBridge signals the execution phase. Lambda monitors the shipment, updates status, and publishes `shipment.status-changed`.

5. If an exception is detected, Lambda publishes `exception.detected`. EventBridge triggers the Intelligence Agent. The agent checks episodic memory for past resolutions. If it can auto-resolve, it publishes `exception.auto-resolved`. If it needs a human, it publishes `hitl.escalation.requested`, which the Transportation Cockpit picks up. The human makes a decision, and `hitl.decision.made` flows back.

6. Lambda publishes `shipment.delivered`. EventBridge triggers the costing phase. Lambda calculates charges, generates an invoice, and publishes `invoice.generated`.

7. Step Functions stores carrier performance and route variance in episodic memory. Lambda publishes `kpi.updated`, and the Transportation Cockpit refreshes its dashboard.

**Key takeaway:** EventBridge is the nervous system. No component calls another directly — they publish events and subscribe to the ones they care about. This makes the system loosely coupled: you can add new consumers (like a notification service) without changing existing components.

---

## Diagram 8: Cost Comparison — 7 Agents vs 3 Agents + Step Functions

**What it shows:** Why we consolidated from 7 agents to 3, and what the practical impact is.

**How to read it:**

**Before (7 Agents):**
- Every operation — even simple CRUD — went through an AI agent
- 7 AgentCore Runtime instances running (and costing money)
- Token costs on transactional operations that didn't need AI
- Higher complexity to manage, debug, and monitor

**After (3 Agents + Step Functions):**
- ~80-90% of operations go through Step Functions + Lambda at zero AI cost
- Only 3 AgentCore Runtime instances
- AI tokens are spent only on operations that genuinely need reasoning
- Simpler to adopt, operate, and explain to customers

**Key takeaway:** The consolidation isn't just about cost — it's about making the system approachable for 3PL customers who are just starting their agentic AI journey. Fewer agents means less to understand, less to configure, and less to pay for. The Step Functions backbone gives them familiar AWS services they likely already know, with AI as an incremental add-on rather than a prerequisite for everything.

---

## How to Read These Diagrams Together

Start with **Diagram 1** for the big picture. Then read **Diagram 2** to understand the fundamental split between deterministic and AI operations. **Diagram 3** tells you what each agent actually does and when it uses cheap vs expensive models. **Diagram 4** is the most important one — it shows the complete shipment journey and exactly where AI gets involved. **Diagram 5** explains the model routing logic. **Diagram 6** is your deployment reference. **Diagram 7** shows how events tie everything together. **Diagram 8** justifies the architectural decision.

The core message across all diagrams: **AI is the exception, not the rule.** The system is designed so that a 3PL customer pays for AI only when it genuinely adds value.
