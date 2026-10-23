# Enterprise TMS - Architecture Diagrams (Consolidated)

> **Scope:** These diagrams depict the architecture the repository deploys via `cdk deploy` — including the Next.js cockpit (S3 + CloudFront, Cognito auth), API Gateway, Kinesis, S3, AppSync (real-time UI), and AgentCore Memory. QuickSight dashboards are an optional documented follow-on connected to the deployed Athena tables (not provisioned by the stack). See [README-MVP.md](../README-MVP.md).

## Design Philosophy

Most TMS operations are deterministic and transactional — they don't need AI. This architecture uses Step Functions + Lambda for the deterministic backbone (~80-90% of operations at zero AI cost) and only 3 AI agents for tasks that genuinely require reasoning. SLMs handle most reasoning cheaply; LLMs are reserved for complex multi-constraint problems.

---

## 1. High-Level System Architecture (Consolidated)

```mermaid
graph TB
    subgraph "External Clients"
        SP[Shipper Portal]
        CP[Carrier Portal]
        LP[LSP Portal]
        TC[Transportation Cockpit<br/>Next.js — HITL UI]
    end

    subgraph "API Layer"
        APIGW[API Gateway<br/>REST APIs]
    end

    subgraph "Deterministic Backbone — Step Functions + Lambda"
        SF_LIFE[Shipment Lifecycle<br/>Step Functions]
        SF_EXC[Exception Handling<br/>Step Functions]
        SF_TEND[Tender & Auction<br/>Step Functions]

        subgraph "Lambda Domain Tools — Zero AI Cost"
            LF_ORD[Order & Freight Unit Tools]
            LF_SHIP[Shipment Tracking Tools]
            LF_TEND[Carrier & Tendering Tools]
            LF_COST[Costing & Invoice Tools]
            LF_KPI[KPI & Metrics Tools]
        end
    end

    subgraph "AI Agents — Only Where Reasoning Is Needed"
        subgraph "AgentCore Runtime"
            AG_PLAN[Planning & Optimization Agent<br/>Route optimization, 3D load,<br/>consolidation, re-planning]
            AG_INTEL[Intelligence Agent<br/>Exception root-cause, anomaly detection,<br/>NL queries, demand forecasting]
            AG_ORCH[Orchestrator Agent<br/>Request classification,<br/>conflict resolution]
        end
    end

    subgraph "AgentCore Services"
        GW[Gateway<br/>Lambda→MCP + Model Routing + Cedar]
        MEM[Memory<br/>Short-term / Episodic / Semantic]
        POL[Policy + Identity + Observability]
    end

    subgraph "Amazon Bedrock — Models"
        SLM[SLM Tier<br/>Nova Micro / Lite]
        LLM[LLM Tier<br/>Claude / Nova Pro]
    end

    subgraph "Event Backbone"
        EB[Amazon EventBridge<br/>Domain Events]
    end

    subgraph "Data Layer"
        DDB[(DynamoDB)]
        KIN[Kinesis<br/>IoT/Telematics]
        S3[(S3<br/>Archive + Analytics)]
    end

    subgraph "External Simulators — Lambda"
        SIM[Carrier Rates / GPS /<br/>Warehouse / IoT Sensors]
    end

    SP & CP & LP & TC --> APIGW
    APIGW --> AG_ORCH
    APIGW --> SF_LIFE

    AG_ORCH -->|Complex request| AG_PLAN
    AG_ORCH -->|Complex request| AG_INTEL
    AG_ORCH -->|Simple/transactional| SF_LIFE

    SF_LIFE --> LF_ORD & LF_SHIP & LF_TEND & LF_COST
    SF_TEND --> LF_TEND
    SF_EXC --> LF_SHIP

    SF_LIFE -->|Needs optimization| AG_PLAN
    SF_EXC -->|Needs root-cause| AG_INTEL

    AG_PLAN & AG_INTEL --> GW
    GW --> SLM & LLM
    GW --> LF_ORD & LF_SHIP & LF_TEND & LF_COST & LF_KPI
    AG_PLAN & AG_INTEL --> MEM

    LF_ORD & LF_SHIP & LF_TEND & LF_COST --> DDB
    LF_SHIP --> KIN
    LF_KPI --> S3
    LF_TEND --> SIM

    EB <--> SF_LIFE & SF_EXC & SF_TEND
    EB --> AG_PLAN & AG_INTEL
    EB --> TC
```

---

## 2. Deterministic vs AI Split — What Goes Where

```mermaid
flowchart TD
    REQ[Incoming TMS Request] --> CLASS{Request Type?}

    CLASS -->|Transactional / CRUD| SF_PATH[Step Functions + Lambda<br/>Zero AI Cost]
    CLASS -->|Needs Reasoning| AGENT_PATH[AI Agent<br/>AgentCore Runtime]

    subgraph "Step Functions Path — ~80-90% of Operations"
        SF_PATH --> SF_OPS
        SF_OPS[Deterministic Operations]
        SF_OPS --> OP1[Create/update freight units]
        SF_OPS --> OP2[Shipment status updates]
        SF_OPS --> OP3[Distribute tenders to carriers]
        SF_OPS --> OP4[Enforce tender deadlines]
        SF_OPS --> OP5[Assign carrier to shipment]
        SF_OPS --> OP6[Calculate charges from rate cards]
        SF_OPS --> OP7[Generate invoices]
        SF_OPS --> OP8[Settlement summaries]
        SF_OPS --> OP9[Compute KPIs from data]
        SF_OPS --> OP10[Publish events to EventBridge]
        SF_OPS --> OP11[Audit logging]
        SF_OPS --> OP12[Validate business rules]
    end

    subgraph "AI Agent Path — ~10-20% of Operations"
        AGENT_PATH --> AG_OPS
        AG_OPS[Reasoning Required]
        AG_OPS --> AI1[Multi-stop route optimization]
        AG_OPS --> AI2[3D load planning]
        AG_OPS --> AI3[Dynamic re-planning on disruptions]
        AG_OPS --> AI4[Exception root-cause analysis]
        AG_OPS --> AI5[Anomaly pattern detection]
        AG_OPS --> AI6[Natural language queries]
        AG_OPS --> AI7[Demand forecasting]
        AG_OPS --> AI8[Complex carrier analysis]
        AG_OPS --> AI9[Load consolidation optimization]
        AG_OPS --> AI10[Ambiguous request classification]
    end

    style SF_PATH fill:#2d6a4f,color:#fff
    style AGENT_PATH fill:#e63946,color:#fff
    style SF_OPS fill:#2d6a4f,color:#fff
    style AG_OPS fill:#e63946,color:#fff
```

---

## 3. The Three AI Agents — Scope and Model Usage

```mermaid
flowchart LR
    subgraph "Orchestrator Agent"
        direction TB
        O_SLM[SLM — Nova Micro]
        O_SLM --> O1[Request classification]
        O_SLM --> O2[Domain routing]
        O_SLM --> O3[Priority assignment]
        O_LLM[LLM — Claude]
        O_LLM --> O4[Ambiguous request interpretation]
        O_LLM --> O5[Multi-agent conflict resolution]
    end

    subgraph "Planning & Optimization Agent"
        direction TB
        P_SLM[SLM — Nova Lite]
        P_SLM --> P1[Single-stop route queries]
        P_SLM --> P2[Simple constraint validation]
        P_LLM[LLM — Claude]
        P_LLM --> P3[Multi-stop route optimization]
        P_LLM --> P4[3D load planning]
        P_LLM --> P5[Dynamic re-planning]
        P_LLM --> P6[Consolidation analysis]
    end

    subgraph "Intelligence Agent"
        direction TB
        I_SLM[SLM — Nova Lite]
        I_SLM --> I1[Exception classification]
        I_SLM --> I2[Simple anomaly detection]
        I_SLM --> I3[Basic NL query parsing]
        I_LLM[LLM — Claude]
        I_LLM --> I4[Exception root-cause analysis]
        I_LLM --> I5[Complex anomaly patterns]
        I_LLM --> I6[NL → SQL translation]
        I_LLM --> I7[Demand forecasting]
        I_LLM --> I8[Contract analysis]
    end

    style O_SLM fill:#1d3557,color:#fff
    style O_LLM fill:#e63946,color:#fff
    style P_SLM fill:#1d3557,color:#fff
    style P_LLM fill:#e63946,color:#fff
    style I_SLM fill:#1d3557,color:#fff
    style I_LLM fill:#e63946,color:#fff
```

---

## 4. Shipment Lifecycle — Step Functions Backbone with AI Handoff

```mermaid
sequenceDiagram
    participant Client
    participant API as API Gateway
    participant SF as Step Functions<br/>Shipment Lifecycle
    participant LF as Lambda Tools<br/>Deterministic
    participant DB as DynamoDB
    participant EB as EventBridge
    participant PLAN as Planning Agent<br/>AgentCore Runtime
    participant INTEL as Intelligence Agent<br/>AgentCore Runtime
    participant GW as AgentCore Gateway
    participant SLM as SLM
    participant LLM as LLM
    participant MEM as AgentCore Memory
    participant TC as Transportation Cockpit

    Client->>API: Submit transportation demand
    API->>SF: Start shipment lifecycle workflow

    Note over SF,LF: STEP 1: Order Ingestion — Deterministic
    SF->>LF: Parse demand, classify shipment type
    LF->>DB: Create freight units
    LF-->>SF: Freight units created
    SF->>LF: Validate against business rules
    LF-->>SF: Validation result

    alt Validation failed
        SF->>LF: Attempt auto-correction (rule-based)
        alt Cannot auto-correct
            SF->>EB: hitl.escalation.requested
            EB->>TC: Display for human review
        end
    end

    SF->>EB: freight-unit.created

    Note over SF,PLAN: STEP 2: Route Planning — AI Handoff
    SF->>PLAN: Optimize route (needs reasoning)
    PLAN->>MEM: Query episodic (past route outcomes)
    PLAN->>GW: get_freight_units (MCP tool)
    GW->>LF: Invoke Lambda
    LF->>DB: Query data
    LF-->>GW: Data
    GW-->>PLAN: Freight unit data
    PLAN->>GW: Route optimization (reasoning)
    GW->>GW: Classify complexity
    alt Simple (≤5 stops)
        GW->>SLM: Nova Lite
    else Complex (>5 stops)
        GW->>LLM: Claude
    end
    GW-->>PLAN: Optimized route
    PLAN->>MEM: Store episodic (route outcome)
    PLAN-->>SF: Route result
    SF->>LF: Save route to DynamoDB
    SF->>EB: route.optimized

    Note over SF,LF: STEP 3: Tendering — Deterministic
    SF->>LF: Get carrier rates, rank carriers
    LF->>DB: Query carrier data
    LF-->>SF: Ranked carriers
    SF->>LF: Create tender, distribute to carriers
    SF->>SF: Wait for responses (timeout)
    alt Responses received
        SF->>LF: Select winner, assign carrier
        LF->>DB: Update shipment with carrier
        SF->>EB: carrier.assigned
    else No responses
        SF->>EB: hitl.escalation.requested
        EB->>TC: Manual carrier assignment
    end

    Note over SF,LF: STEP 4: Execution Monitoring — Deterministic + AI
    SF->>LF: Monitor shipment status (Kinesis consumer)
    LF->>DB: Update status
    SF->>EB: shipment.status-changed

    alt Exception detected
        SF->>INTEL: Classify and analyze exception
        INTEL->>GW: Exception analysis (reasoning)
        alt Auto-resolvable (SLM)
            GW->>SLM: Classify severity
            INTEL-->>SF: Auto-resolve
        else Complex (LLM needed)
            GW->>LLM: Root-cause analysis
            INTEL-->>SF: Resolution or escalate to HITL
        end
    end

    SF->>EB: shipment.delivered

    Note over SF,LF: STEP 5: Costing — Deterministic
    SF->>LF: Get rate card, calculate charges
    LF->>DB: Query rate cards
    LF-->>SF: Charges calculated
    SF->>LF: Generate invoice
    LF->>DB: Persist invoice
    SF->>EB: invoice.generated

    Note over SF,LF: STEP 6: Learning — Deterministic + Memory
    SF->>LF: Update carrier performance score
    LF->>DB: Update carrier record
    SF->>MEM: Store episodic (carrier outcome, route variance)
```

---

## 5. SLM/LLM Decision Flow (via AgentCore Gateway)

```mermaid
flowchart TD
    REQ[Request arrives at AI Agent] --> MCP_CHECK{MCP Tool available?<br/>Deterministic operation?}

    MCP_CHECK -->|Yes| GW_TOOL[AgentCore Gateway<br/>invokes Lambda Tool<br/>Zero token cost]
    MCP_CHECK -->|No, needs reasoning| GW_ROUTE{Gateway Model Routing:<br/>Complexity?}

    GW_TOOL --> CEDAR{Cedar Policy Check}
    CEDAR -->|Allowed| EXEC[Execute Tool → Return]
    CEDAR -->|Denied| HITL[Escalate to HITL]

    GW_ROUTE -->|Low| SLM[SLM — Nova Micro/Lite<br/>Fast, cheap]
    GW_ROUTE -->|High| LLM[LLM — Claude/Nova Pro<br/>Complex reasoning]

    SLM --> CONF{Confidence ≥ 0.75?}
    CONF -->|Yes| DONE[Return Response]
    CONF -->|No| LLM

    LLM --> DONE

    DONE --> MEM_STORE[Store in AgentCore Memory]

    style GW_TOOL fill:#2d6a4f,color:#fff
    style SLM fill:#1d3557,color:#fff
    style LLM fill:#e63946,color:#fff
```

---

## 6. AWS Deployment Architecture (Consolidated)

```mermaid
graph TB
    subgraph "Fully Managed — AgentCore"
        subgraph "AgentCore Runtime — 3 Agents"
            AC_ORCH[Orchestrator Agent]
            AC_PLAN[Planning & Optimization Agent]
            AC_INTEL[Intelligence Agent]
        end
        AGW[AgentCore Gateway<br/>MCP + Model Routing + Cedar]
        AC_MEM[AgentCore Memory<br/>Short-term / Episodic / Semantic]
        AC_SEC[Identity + Observability + Policy]
    end

    subgraph "Amazon Bedrock — Models"
        BR_SLM[Nova Micro / Lite — SLM]
        BR_LLM[Claude / Nova Pro — LLM]
    end

    subgraph "Step Functions — Deterministic Workflows"
        SF1[Shipment Lifecycle Workflow]
        SF2[Exception Handling Workflow]
        SF3[Tender & Auction Workflow]
    end

    subgraph "Lambda — Domain Tools"
        LF1[Order & Freight Unit]
        LF2[Shipment Tracking]
        LF3[Carrier & Tendering]
        LF4[Costing & Invoice]
        LF5[KPI & Metrics]
    end

    subgraph "Lambda — Simulators"
        SIM1[Carrier Rates]
        SIM2[GPS / Telematics]
        SIM3[Warehouse]
        SIM4[IoT Sensors]
    end

    subgraph "Event & Data"
        EB[EventBridge]
        DDB[(DynamoDB)]
        KIN[Kinesis]
        S3[(S3)]
    end

    subgraph "Analytics & UI"
        QS[QuickSight]
        ATH[Athena]
        TC[Transportation Cockpit<br/>Next.js]
    end

    subgraph "Infrastructure as Code"
        CDK[AWS CDK — Python]
    end

    TC --> AC_ORCH
    AC_ORCH --> SF1 & AC_PLAN & AC_INTEL
    SF1 & SF2 & SF3 --> LF1 & LF2 & LF3 & LF4
    SF1 -->|Needs reasoning| AC_PLAN
    SF2 -->|Needs reasoning| AC_INTEL

    AC_PLAN & AC_INTEL --> AGW
    AGW --> BR_SLM & BR_LLM
    AGW --> LF1 & LF2 & LF3 & LF4 & LF5
    AC_PLAN & AC_INTEL --> AC_MEM

    LF1 & LF2 & LF3 & LF4 --> DDB
    LF2 --> KIN
    LF5 --> S3 & ATH
    S3 --> QS
    LF3 --> SIM1
    LF2 --> SIM2 & SIM4
    LF1 --> SIM3

    EB <--> SF1 & SF2 & SF3
    EB --> AC_PLAN & AC_INTEL & TC

    CDK -.-> LF1 & LF2 & LF3 & LF4 & LF5 & DDB & S3 & EB & KIN
```

---

## 7. Event-Driven Flow (Consolidated)

```mermaid
sequenceDiagram
    participant SF as Step Functions<br/>Shipment Lifecycle
    participant LF as Lambda Tools
    participant EB as EventBridge
    participant PLAN as Planning Agent
    participant INTEL as Intelligence Agent
    participant MEM as AgentCore Memory
    participant TC as Transportation Cockpit

    Note over SF,TC: End-to-End Shipment Lifecycle

    SF->>LF: Ingest order, create freight units
    LF->>EB: freight-unit.created
    EB->>PLAN: Trigger route optimization

    PLAN->>MEM: Query episodic (similar routes)
    PLAN->>EB: route.optimized
    PLAN->>MEM: Store episodic (route outcome)

    EB->>SF: Continue → Tendering phase
    SF->>LF: Distribute tender, wait for responses
    LF->>EB: carrier.assigned

    EB->>SF: Continue → Execution phase
    SF->>LF: Monitor shipment, update status
    LF->>EB: shipment.status-changed

    alt Exception detected
        LF->>EB: exception.detected
        EB->>INTEL: Analyze exception
        INTEL->>MEM: Query episodic (past resolutions)
        alt Auto-resolvable
            INTEL->>EB: exception.auto-resolved
        else Needs human
            INTEL->>EB: hitl.escalation.requested
            EB->>TC: Display escalation
            TC->>EB: hitl.decision.made
        end
    end

    LF->>EB: shipment.delivered
    EB->>SF: Continue → Costing phase
    SF->>LF: Calculate charges, generate invoice
    LF->>EB: invoice.generated

    SF->>MEM: Store episodic (carrier performance, route variance)
    LF->>EB: kpi.updated
    EB->>TC: Refresh dashboard
```

---

## 8. Cost Comparison — 7 Agents vs 3 Agents + Step Functions

```mermaid
flowchart LR
    subgraph "Before: 7 Agents"
        direction TB
        B1[Every operation goes<br/>through an AI agent]
        B2[7 AgentCore Runtime instances]
        B3[Token cost on<br/>transactional operations]
        B4[Higher complexity<br/>to manage]
    end

    subgraph "After: 3 Agents + Step Functions"
        direction TB
        A1[~80-90% operations<br/>via Step Functions<br/>Zero AI cost]
        A2[3 AgentCore Runtime instances]
        A3[AI tokens only for<br/>reasoning tasks]
        A4[Simpler to adopt<br/>and operate]
    end

    B1 -.->|Optimized to| A1
    B2 -.->|Reduced to| A2
    B3 -.->|Eliminated for| A3
    B4 -.->|Simplified to| A4

    style B1 fill:#e63946,color:#fff
    style B2 fill:#e63946,color:#fff
    style B3 fill:#e63946,color:#fff
    style B4 fill:#e63946,color:#fff
    style A1 fill:#2d6a4f,color:#fff
    style A2 fill:#2d6a4f,color:#fff
    style A3 fill:#2d6a4f,color:#fff
    style A4 fill:#2d6a4f,color:#fff
```
