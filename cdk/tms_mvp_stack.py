"""TMS MVP Demo — CDK Stack definition.

This stack defines all AWS resources for the TMS MVP demonstration:
DynamoDB table, Lambda functions, Step Functions workflow, EventBridge bus,
SQS progress queue, and AgentCore agent configurations.

Deployed via: cdk deploy
"""

import os

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    CustomResource,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_kinesis as kinesis,
    aws_lambda as _lambda,
    aws_lambda_event_sources as lambda_event_sources,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_apigateway as apigateway,
    aws_appsync as appsync,
    aws_cognito as cognito,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as cloudfront_origins,
    aws_glue as glue,
    aws_athena as athena,
    aws_bedrockagentcore as agentcore,
    aws_sqs as sqs,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as sfn_tasks,
    custom_resources as cr,
)
from constructs import Construct

from shared.constants import (
    AGENTCORE_MEMORY_NAME,
    EVENT_BUS_NAME,
    EVENT_SOURCE,
    SQS_QUEUE_NAME,
)

# Common excludes for Lambda asset bundling (project root is bundled into each
# function). Keep heavy / non-Python trees out of the deployment package.
LAMBDA_ASSET_EXCLUDES = [
    "cdk.out",
    ".venv",
    ".git",
    "tests",
    "__pycache__",
    "*.pyc",
    "frontend",
    "node_modules",
    "docs",
    "*.md",
    "*.png",
    "*.docx",
    ".hypothesis",
    ".pytest_cache",
]


class TmsMvpStack(Stack):
    """CDK Stack for the TMS MVP Demo infrastructure.

    Resources are added incrementally across implementation tasks:
    - Task 1.3: DynamoDB table + seed data Custom Resource
    - Task 3.8: Lambda functions (6 tools + 1 simulator + 2 agent wrappers)
    - Task 6.1: Step Functions shipment lifecycle workflow
    - Task 6.2: EventBridge bus + SQS progress queue
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Shared foundational resources (created first; consumers below) ---

        # 1. DynamoDB single-table store
        self.table = self._create_dynamodb_table()

        # 2. S3: document storage + domain-event/outcome archive (Athena source)
        self.documents_bucket, self.archive_bucket = self._create_storage()

        # 3. Kinesis stream for execution/telematics tracking (blog Tier 1, step 4)
        self.telematics_stream = self._create_telematics_stream()

        # 4. EventBridge bus — the domain-event backbone all consumers hang off
        self.event_bus = self._create_event_bus()

        # --- Compute + workflow ---

        # 4b. Lambda layer with the AI-agent runtime deps (strands, pydantic).
        self.agent_layer = self._create_agent_layer()

        # 5. Lambda tools (6 deterministic + simulator + 2 agent wrappers)
        self.lambdas = self._create_lambda_functions()

        # 6. Step Functions shipment lifecycle workflow
        self.workflow = self._create_shipment_workflow()

        # --- Event consumers / fan-out (all read from the event bus) ---

        # 7. SQS progress queue (legacy/simple polling consumer)
        self.progress_queue = self._create_progress_queue()

        # 8. S3 archiver: every domain event -> archive bucket (Athena queryable)
        self._create_event_archiver()

        # 9. Kinesis telematics processor Lambda (consumes the stream)
        self._create_telematics_processor()

        # 10. AgentCore Memory resource for continuous-learning outcomes
        self.memory = self._create_agentcore_memory()
        self._grant_memory_access()

        # 11. AppSync real-time API + event publisher (browser subscriptions)
        self.realtime_api = self._create_realtime_api()

        # 12. Orchestrator Lambda + API Gateway (HTTP ingress)
        self.rest_api = self._create_api()

        # 13. Athena/Glue over the S3 event archive
        self._create_analytics()

        # 14. Next.js cockpit hosted on S3 + CloudFront (built at deploy time)
        self._create_frontend_hosting()

        # 15. Seed data Custom Resource
        self._populate_seed_data()

    def _create_lambda_functions(self) -> dict[str, _lambda.Function]:
        """Create all 7 Lambda functions for the TMS MVP Demo.

        Functions:
        - tms-create-freight-unit: Creates freight unit records
        - tms-carrier-simulator: Returns simulated carrier rate quotes
        - tms-assign-carrier: Assigns best carrier to a shipment
        - tms-track-shipment: Updates shipment tracking status
        - tms-detect-exception: Detects shipment exceptions
        - tms-calculate-charges: Calculates freight charges
        - tms-complete-shipment: Transitions shipment to complete

        All functions get DynamoDB read/write and EventBridge put-events permissions.
        The assign-carrier function additionally gets invoke permission on carrier-simulator.
        """
        # Project root directory (parent of cdk/)
        project_root = os.path.join(os.path.dirname(__file__), "..")

        # Common environment variables for all Lambda functions
        common_env = {
            "TABLE_NAME": self.table.table_name,
            "EVENT_BUS_NAME": EVENT_BUS_NAME,
        }

        # EventBridge put-events policy (shared across all functions)
        eventbridge_put_policy = iam.PolicyStatement(
            actions=["events:PutEvents"],
            resources=[
                f"arn:aws:events:{self.region}:{self.account}:event-bus/{EVENT_BUS_NAME}"
            ],
        )

        # --- 1. Create Freight Unit ---
        create_freight_unit_fn = _lambda.Function(
            self,
            "CreateFreightUnitFunction",
            function_name="tms-create-freight-unit",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/create_freight_unit/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(30),
            environment=common_env,
        )
        self.table.grant_read_write_data(create_freight_unit_fn)
        create_freight_unit_fn.add_to_role_policy(eventbridge_put_policy)

        # --- 2. Carrier Simulator ---
        carrier_simulator_fn = _lambda.Function(
            self,
            "CarrierSimulatorFunction",
            function_name="tms-carrier-simulator",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/carrier_simulator/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(30),
            environment=common_env,
        )
        self.table.grant_read_write_data(carrier_simulator_fn)
        carrier_simulator_fn.add_to_role_policy(eventbridge_put_policy)

        # --- 3. Assign Carrier ---
        assign_carrier_fn = _lambda.Function(
            self,
            "AssignCarrierFunction",
            function_name="tms-assign-carrier",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/assign_carrier/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(30),
            environment={
                **common_env,
                "CARRIER_SIMULATOR_FUNCTION": carrier_simulator_fn.function_name,
            },
        )
        self.table.grant_read_write_data(assign_carrier_fn)
        assign_carrier_fn.add_to_role_policy(eventbridge_put_policy)
        # Grant assign-carrier permission to invoke carrier-simulator
        carrier_simulator_fn.grant_invoke(assign_carrier_fn)

        # --- 4. Track Shipment ---
        track_shipment_fn = _lambda.Function(
            self,
            "TrackShipmentFunction",
            function_name="tms-track-shipment",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/track_shipment/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(30),
            environment=common_env,
        )
        self.table.grant_read_write_data(track_shipment_fn)
        track_shipment_fn.add_to_role_policy(eventbridge_put_policy)

        # --- 5. Detect Exception ---
        detect_exception_fn = _lambda.Function(
            self,
            "DetectExceptionFunction",
            function_name="tms-detect-exception",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/detect_exception/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(30),
            environment=common_env,
        )
        self.table.grant_read_write_data(detect_exception_fn)
        detect_exception_fn.add_to_role_policy(eventbridge_put_policy)

        # --- 6. Calculate Charges ---
        calculate_charges_fn = _lambda.Function(
            self,
            "CalculateChargesFunction",
            function_name="tms-calculate-charges",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/calculate_charges/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(30),
            environment=common_env,
        )
        self.table.grant_read_write_data(calculate_charges_fn)
        calculate_charges_fn.add_to_role_policy(eventbridge_put_policy)

        # --- 7. Complete Shipment ---
        complete_shipment_fn = _lambda.Function(
            self,
            "CompleteShipmentFunction",
            function_name="tms-complete-shipment",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/complete_shipment/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(30),
            environment=common_env,
        )
        self.table.grant_read_write_data(complete_shipment_fn)
        complete_shipment_fn.add_to_role_policy(eventbridge_put_policy)

        # Bedrock invoke policy (shared by agent wrapper Lambdas).
        # The Strands SDK uses the streaming Converse API against cross-region
        # INFERENCE PROFILES (e.g. us.amazon.nova-micro-v1:0), which in turn
        # route to the underlying foundation models in multiple regions. The
        # policy must therefore allow both InvokeModel and
        # InvokeModelWithResponseStream on BOTH inference-profile ARNs and the
        # foundation-model ARNs across regions.
        bedrock_invoke_policy = iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                "arn:aws:bedrock:*::foundation-model/*",
                f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
            ],
        )

        # --- 8. Invoke Planning Agent (wrapper for Step Functions handoff) ---
        invoke_planning_agent_fn = _lambda.Function(
            self,
            "InvokePlanningAgentFunction",
            function_name="tms-invoke-planning-agent",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/invoke_planning_agent/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(60),
            environment=common_env,
            layers=[self.agent_layer],
        )
        self.table.grant_read_write_data(invoke_planning_agent_fn)
        invoke_planning_agent_fn.add_to_role_policy(eventbridge_put_policy)
        invoke_planning_agent_fn.add_to_role_policy(bedrock_invoke_policy)

        # --- 9. Invoke Intelligence Agent (wrapper for Step Functions handoff) ---
        invoke_intelligence_agent_fn = _lambda.Function(
            self,
            "InvokeIntelligenceAgentFunction",
            function_name="tms-invoke-intelligence-agent",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/invoke_intelligence_agent/handler.handler",
            code=_lambda.Code.from_asset(
                project_root,
                exclude=LAMBDA_ASSET_EXCLUDES,
            ),
            timeout=Duration.seconds(60),
            environment=common_env,
            layers=[self.agent_layer],
        )
        self.table.grant_read_write_data(invoke_intelligence_agent_fn)
        invoke_intelligence_agent_fn.add_to_role_policy(eventbridge_put_policy)
        invoke_intelligence_agent_fn.add_to_role_policy(bedrock_invoke_policy)

        return {
            "create_freight_unit": create_freight_unit_fn,
            "carrier_simulator": carrier_simulator_fn,
            "assign_carrier": assign_carrier_fn,
            "track_shipment": track_shipment_fn,
            "detect_exception": detect_exception_fn,
            "calculate_charges": calculate_charges_fn,
            "complete_shipment": complete_shipment_fn,
            "invoke_planning_agent": invoke_planning_agent_fn,
            "invoke_intelligence_agent": invoke_intelligence_agent_fn,
        }

    def _create_event_bus(self) -> events.EventBus:
        """Create the EventBridge custom event bus for TMS domain events.

        Bus name: tms-mvp-events (from shared/constants.py EVENT_BUS_NAME)
        All lifecycle events (ShipmentCreated, RouteOptimized, CarrierAssigned,
        ExceptionDetected, ExceptionResolved, ShipmentCompleted) publish here
        with source "tms.mvp".

        Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
        """
        bus = events.EventBus(
            self,
            "TmsMvpEventBus",
            event_bus_name=EVENT_BUS_NAME,
        )
        bus.apply_removal_policy(RemovalPolicy.DESTROY)
        return bus

    def _create_progress_queue(self) -> sqs.Queue:
        """Create the SQS progress queue (a simple polling consumer for domain events).

        The queue receives all tms.mvp events via an EventBridge rule.
        Useful for scripts or downstream systems that want to poll lifecycle
        progress; the Transportation Cockpit uses AppSync for real-time updates.

        Configuration:
        - Queue name: tms-mvp-progress (from shared/constants.py SQS_QUEUE_NAME)
        - Visibility timeout: 30s (appropriate for a short polling interval)
        - Message retention: 1 day (demo only, no long-term replay needed)
        - Dead-letter queue: captures failed deliveries after 3 receive attempts
        - Removal policy: DESTROY (demo resources, clean up on stack deletion)

        Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
        """
        # Dead-letter queue for failed message deliveries
        dlq = sqs.Queue(
            self,
            "TmsMvpProgressDLQ",
            queue_name=f"{SQS_QUEUE_NAME}-dlq",
            retention_period=Duration.days(7),
            removal_policy=RemovalPolicy.DESTROY,
        )

        queue = sqs.Queue(
            self,
            "TmsMvpProgressQueue",
            queue_name=SQS_QUEUE_NAME,
            visibility_timeout=Duration.seconds(30),
            retention_period=Duration.days(1),
            removal_policy=RemovalPolicy.DESTROY,
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=dlq,
            ),
        )

        # EventBridge rule: route ALL tms.mvp events to the SQS queue
        rule = events.Rule(
            self,
            "TmsMvpEventsToSqsRule",
            rule_name="tms-mvp-events-to-sqs",
            event_bus=self.event_bus,
            event_pattern=events.EventPattern(
                source=[EVENT_SOURCE],
            ),
        )
        rule.add_target(targets.SqsQueue(queue))

        # Output the SQS queue URL for polling consumers (scripts, downstream systems)
        CfnOutput(
            self,
            "ProgressQueueUrl",
            value=queue.queue_url,
            description="SQS progress queue URL (polling consumer for tms.mvp domain events)",
            export_name="TmsMvpProgressQueueUrl",
        )

        return queue

    def _create_shipment_workflow(self) -> sfn.StateMachine:
        """Create the Step Functions shipment lifecycle state machine.

        8-state linear workflow:
        CreateFreightUnit → OptimizeRoute → AssignCarrier → TrackShipment →
        DetectException → AnalyzeException → CalculateCharges → CompleteShipment

        - Deterministic steps: Direct Lambda invocations with 2 retries
        - Agent handoff steps: Lambda wrapper invocations with 60s timeout
        - Each state passes its output to the next state via result_path

        Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
        """
        # --- Step 1: CreateFreightUnit (Lambda direct, 2 retries) ---
        create_freight_unit = sfn_tasks.LambdaInvoke(
            self,
            "CreateFreightUnit",
            lambda_function=self.lambdas["create_freight_unit"],
            payload=sfn.TaskInput.from_json_path_at("$"),
            result_path="$.create_freight_unit_result",
            result_selector={
                "shipment_id.$": "$.Payload.shipment_id",
                "freight_unit_id.$": "$.Payload.freight_unit_id",
                "status.$": "$.Payload.status",
                "created_at.$": "$.Payload.created_at",
            },
            retry_on_service_exceptions=True,
        )
        create_freight_unit.add_retry(
            errors=["States.TaskFailed", "Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Step 2: OptimizeRoute (Agent handoff via wrapper Lambda, 60s timeout) ---
        optimize_route = sfn_tasks.LambdaInvoke(
            self,
            "OptimizeRoute",
            lambda_function=self.lambdas["invoke_planning_agent"],
            payload=sfn.TaskInput.from_object(
                {
                    "shipment_id": sfn.JsonPath.string_at(
                        "$.create_freight_unit_result.shipment_id"
                    ),
                    "origin": sfn.JsonPath.string_at("$.origin"),
                    "destination": sfn.JsonPath.string_at("$.destination"),
                    "weight_kg": sfn.JsonPath.number_at("$.weight_kg"),
                }
            ),
            result_path="$.optimize_route_result",
            result_selector={
                "shipment_id.$": "$.Payload.shipment_id",
                "route.$": "$.Payload.route",
                "confidence.$": "$.Payload.confidence",
                "model_tier.$": "$.Payload.model_tier",
            },
            task_timeout=sfn.Timeout.duration(Duration.seconds(60)),
        )
        optimize_route.add_retry(
            errors=["States.TaskFailed", "Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Step 3: AssignCarrier (Lambda direct, 2 retries) ---
        assign_carrier = sfn_tasks.LambdaInvoke(
            self,
            "AssignCarrier",
            lambda_function=self.lambdas["assign_carrier"],
            payload=sfn.TaskInput.from_object(
                {
                    "shipment_id": sfn.JsonPath.string_at(
                        "$.create_freight_unit_result.shipment_id"
                    ),
                    "origin": sfn.JsonPath.string_at("$.origin"),
                    "destination": sfn.JsonPath.string_at("$.destination"),
                    "weight_kg": sfn.JsonPath.number_at("$.weight_kg"),
                }
            ),
            result_path="$.assign_carrier_result",
            result_selector={
                "carrier_id.$": "$.Payload.carrier_id",
                "carrier_name.$": "$.Payload.carrier_name",
                "rate_usd.$": "$.Payload.rate_usd",
                "service_type.$": "$.Payload.service_type",
            },
            retry_on_service_exceptions=True,
        )
        assign_carrier.add_retry(
            errors=["States.TaskFailed", "Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Step 4: TrackShipment (Lambda direct, 2 retries) ---
        track_shipment = sfn_tasks.LambdaInvoke(
            self,
            "TrackShipment",
            lambda_function=self.lambdas["track_shipment"],
            payload=sfn.TaskInput.from_object(
                {
                    "shipment_id": sfn.JsonPath.string_at(
                        "$.create_freight_unit_result.shipment_id"
                    ),
                }
            ),
            result_path="$.track_shipment_result",
            result_selector={
                "current_status.$": "$.Payload.current_status",
                "location.$": "$.Payload.location",
                "eta.$": "$.Payload.eta",
            },
            retry_on_service_exceptions=True,
        )
        track_shipment.add_retry(
            errors=["States.TaskFailed", "Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Step 5: DetectException (Lambda direct, 2 retries) ---
        detect_exception = sfn_tasks.LambdaInvoke(
            self,
            "DetectException",
            lambda_function=self.lambdas["detect_exception"],
            payload=sfn.TaskInput.from_object(
                {
                    "shipment_id": sfn.JsonPath.string_at(
                        "$.create_freight_unit_result.shipment_id"
                    ),
                }
            ),
            result_path="$.detect_exception_result",
            result_selector={
                "exception_detected.$": "$.Payload.exception_detected",
                "exception_type.$": "$.Payload.exception_type",
                "severity_hint.$": "$.Payload.severity_hint",
            },
            retry_on_service_exceptions=True,
        )
        detect_exception.add_retry(
            errors=["States.TaskFailed", "Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Step 6: AnalyzeException (Agent handoff via wrapper Lambda, 60s timeout) ---
        analyze_exception = sfn_tasks.LambdaInvoke(
            self,
            "AnalyzeException",
            lambda_function=self.lambdas["invoke_intelligence_agent"],
            payload=sfn.TaskInput.from_object(
                {
                    "shipment_id": sfn.JsonPath.string_at(
                        "$.create_freight_unit_result.shipment_id"
                    ),
                    "exception_type": sfn.JsonPath.string_at(
                        "$.detect_exception_result.exception_type"
                    ),
                    "severity_hint": sfn.JsonPath.string_at(
                        "$.detect_exception_result.severity_hint"
                    ),
                }
            ),
            result_path="$.analyze_exception_result",
            result_selector={
                "exception_type.$": "$.Payload.exception_type",
                "severity.$": "$.Payload.severity",
                "resolution_recommendation.$": "$.Payload.resolution_recommendation",
                "confidence.$": "$.Payload.confidence",
                "model_tier.$": "$.Payload.model_tier",
            },
            task_timeout=sfn.Timeout.duration(Duration.seconds(60)),
        )
        analyze_exception.add_retry(
            errors=["States.TaskFailed", "Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Step 7: CalculateCharges (Lambda direct, 2 retries) ---
        calculate_charges = sfn_tasks.LambdaInvoke(
            self,
            "CalculateCharges",
            lambda_function=self.lambdas["calculate_charges"],
            payload=sfn.TaskInput.from_object(
                {
                    "shipment_id": sfn.JsonPath.string_at(
                        "$.create_freight_unit_result.shipment_id"
                    ),
                    "carrier_id": sfn.JsonPath.string_at(
                        "$.assign_carrier_result.carrier_id"
                    ),
                    "distance_km": sfn.JsonPath.number_at(
                        "$.optimize_route_result.route.distance_km"
                    ),
                    "weight_kg": sfn.JsonPath.number_at("$.weight_kg"),
                }
            ),
            result_path="$.calculate_charges_result",
            result_selector={
                "base_charge.$": "$.Payload.base_charge",
                "fuel_surcharge.$": "$.Payload.fuel_surcharge",
                "total_charge.$": "$.Payload.total_charge",
                "currency.$": "$.Payload.currency",
            },
            retry_on_service_exceptions=True,
        )
        calculate_charges.add_retry(
            errors=["States.TaskFailed", "Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Step 8: CompleteShipment (Lambda direct, 2 retries) ---
        complete_shipment = sfn_tasks.LambdaInvoke(
            self,
            "CompleteShipment",
            lambda_function=self.lambdas["complete_shipment"],
            payload=sfn.TaskInput.from_object(
                {
                    "shipment_id": sfn.JsonPath.string_at(
                        "$.create_freight_unit_result.shipment_id"
                    ),
                }
            ),
            result_path="$.complete_shipment_result",
            result_selector={
                "shipment_id.$": "$.Payload.shipment_id",
                "final_status.$": "$.Payload.final_status",
                "completed_at.$": "$.Payload.completed_at",
            },
            retry_on_service_exceptions=True,
        )
        complete_shipment.add_retry(
            errors=["States.TaskFailed", "Lambda.ServiceException"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # Chain all steps in linear sequence
        definition = (
            create_freight_unit.next(optimize_route)
            .next(assign_carrier)
            .next(track_shipment)
            .next(detect_exception)
            .next(analyze_exception)
            .next(calculate_charges)
            .next(complete_shipment)
        )

        # Create the state machine
        state_machine = sfn.StateMachine(
            self,
            "ShipmentLifecycleWorkflow",
            state_machine_name="tms-shipment-lifecycle",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(5),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Grant invoke permissions on all Lambda functions used by the workflow.
        # This is a one-directional edge (state machine -> Lambdas), which is safe.
        for fn in self.lambdas.values():
            fn.grant_invoke(state_machine)

        # NOTE: we deliberately do NOT inject STATE_MACHINE_ARN into the tool /
        # agent-wrapper Lambdas. None of them start executions — only the
        # Orchestrator Lambda does, and it receives STATE_MACHINE_ARN in
        # _create_api(). Injecting the ARN into the workflow's own task Lambdas
        # creates a Lambda <-> StateMachine circular dependency in CloudFormation.

        # Output the state machine ARN
        CfnOutput(
            self,
            "ShipmentWorkflowArn",
            value=state_machine.state_machine_arn,
            description="Step Functions shipment lifecycle workflow ARN",
            export_name="TmsMvpShipmentWorkflowArn",
        )

        return state_machine

    # ------------------------------------------------------------------
    # Lambda layer: AI-agent runtime dependencies (strands, pydantic)
    # ------------------------------------------------------------------
    def _create_agent_layer(self) -> _lambda.LayerVersion:
        """Build a Lambda layer with the agent SDK deps (strands, pydantic).

        The agent Lambdas import ``strands`` (Strands Agents SDK), which is NOT
        part of the Lambda runtime and is not in the bundled project source.
        This layer pip-installs those deps (Linux/py3.12 wheels) at deploy time.
        """
        layer_asset = _lambda.Code.from_asset(
            os.path.join(self._project_root(), "lambdas"),
            bundling={
                "image": _lambda.Runtime.PYTHON_3_12.bundling_image,
                "command": [
                    "bash", "-c",
                    "pip install -r agent_layer_requirements.txt -t /asset-output/python",
                ],
            },
        )
        return _lambda.LayerVersion(
            self,
            "AgentDependenciesLayer",
            code=layer_asset,
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_12],
            description="Strands Agents SDK + pydantic for the TMS AI-agent Lambdas",
        )

    # ------------------------------------------------------------------
    # Storage: S3 document store + domain-event/outcome archive
    # ------------------------------------------------------------------
    def _create_storage(self) -> tuple[s3.Bucket, s3.Bucket]:
        """Create S3 buckets for shipment documents and the event archive.

        - documents_bucket: shipment documents (BOLs, PODs, invoices) — Tier 1
          document storage per the blog.
        - archive_bucket: every domain event written as JSON, partitioned by
          date, so Athena can query shipment history and SLM-vs-LLM usage.

        Both use S3-managed encryption, block all public access, and a DESTROY
        removal policy with auto-delete (demo resources).
        """
        documents_bucket = s3.Bucket(
            self,
            "TmsDocumentsBucket",
            bucket_name=f"tms-mvp-documents-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        archive_bucket = s3.Bucket(
            self,
            "TmsEventArchiveBucket",
            bucket_name=f"tms-mvp-event-archive-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        CfnOutput(
            self,
            "DocumentsBucketName",
            value=documents_bucket.bucket_name,
            description="S3 bucket for shipment documents",
            export_name="TmsMvpDocumentsBucket",
        )
        CfnOutput(
            self,
            "EventArchiveBucketName",
            value=archive_bucket.bucket_name,
            description="S3 bucket for the domain-event archive (Athena source)",
            export_name="TmsMvpEventArchiveBucket",
        )
        return documents_bucket, archive_bucket

    # ------------------------------------------------------------------
    # Kinesis: execution / telematics tracking stream
    # ------------------------------------------------------------------
    def _create_telematics_stream(self) -> kinesis.Stream:
        """Create the Kinesis stream for real-time execution/telematics tracking.

        Per the blog's Tier 1 (lifecycle step 4, Execution monitoring), tracking
        updates flow through Kinesis and are consumed by a Lambda processor that
        updates shipment state. Uses on-demand capacity (no shard management for
        a demo) and a DESTROY removal policy.
        """
        stream = kinesis.Stream(
            self,
            "TmsTelematicsStream",
            stream_name="tms-mvp-telematics",
            stream_mode=kinesis.StreamMode.ON_DEMAND,
            retention_period=Duration.hours(24),
        )
        stream.apply_removal_policy(RemovalPolicy.DESTROY)

        CfnOutput(
            self,
            "TelematicsStreamName",
            value=stream.stream_name,
            description="Kinesis stream for execution/telematics tracking",
            export_name="TmsMvpTelematicsStream",
        )
        return stream

    # ------------------------------------------------------------------
    # Event consumers: S3 archiver + Kinesis telematics processor
    # ------------------------------------------------------------------
    def _project_root(self) -> str:
        return os.path.join(os.path.dirname(__file__), "..")

    def _create_event_archiver(self) -> None:
        """Archive every domain event to S3 via an EventBridge rule → Lambda.

        Adds a second consumer alongside the SQS progress queue: a rule matching
        all ``tms.mvp`` events routes them to an archiver Lambda that writes each
        event as JSON to the archive bucket (Athena-queryable).
        """
        archiver_fn = _lambda.Function(
            self,
            "EventArchiverFunction",
            function_name="tms-archive-event",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/archive_event/handler.handler",
            code=_lambda.Code.from_asset(self._project_root(), exclude=LAMBDA_ASSET_EXCLUDES),
            timeout=Duration.seconds(30),
            environment={"ARCHIVE_BUCKET": self.archive_bucket.bucket_name},
        )
        self.archive_bucket.grant_write(archiver_fn)

        rule = events.Rule(
            self,
            "TmsEventsToArchiveRule",
            rule_name="tms-mvp-events-to-archive",
            event_bus=self.event_bus,
            event_pattern=events.EventPattern(source=[EVENT_SOURCE]),
        )
        rule.add_target(targets.LambdaFunction(archiver_fn))

    def _create_telematics_processor(self) -> None:
        """Create the Lambda that consumes the Kinesis telematics stream."""
        processor_fn = _lambda.Function(
            self,
            "TelematicsProcessorFunction",
            function_name="tms-telematics-processor",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/telematics_processor/handler.handler",
            code=_lambda.Code.from_asset(self._project_root(), exclude=LAMBDA_ASSET_EXCLUDES),
            timeout=Duration.seconds(30),
            environment={"TABLE_NAME": self.table.table_name},
        )
        self.table.grant_read_write_data(processor_fn)
        processor_fn.add_event_source(
            lambda_event_sources.KinesisEventSource(
                self.telematics_stream,
                batch_size=10,
                starting_position=_lambda.StartingPosition.LATEST,
                retry_attempts=2,
            )
        )

    # ------------------------------------------------------------------
    # AgentCore Memory (continuous-learning outcome store)
    # ------------------------------------------------------------------
    def _create_agentcore_memory(self) -> agentcore.CfnMemory:
        """Create the AgentCore Memory resource for storing shipment outcomes.

        Agents write route/exception outcomes as memory events and retrieve
        relevant prior outcomes to condition future decisions (the blog's
        continuous-learning loop). Uses the default (AgentCore-managed) semantic
        strategy; events expire after 90 days for the demo.
        """
        memory = agentcore.CfnMemory(
            self,
            "TmsAgentCoreMemory",
            name=AGENTCORE_MEMORY_NAME,
            description="TMS shipment/route/exception outcomes for continuous learning",
            event_expiry_duration=90,
        )
        memory.apply_removal_policy(RemovalPolicy.DESTROY)

        CfnOutput(
            self,
            "AgentCoreMemoryId",
            value=memory.attr_memory_id,
            description="AgentCore Memory resource ID",
            export_name="TmsMvpAgentCoreMemoryId",
        )
        return memory

    def _grant_memory_access(self) -> None:
        """Give the agent-wrapper Lambdas MEMORY_ID + AgentCore data-plane access.

        The planning and intelligence agents read/write outcomes via the
        ``bedrock-agentcore`` data plane (CreateEvent / RetrieveMemoryRecords).
        """
        memory_policy = iam.PolicyStatement(
            actions=[
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:ListMemoryRecords",
            ],
            resources=[
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/*"
            ],
        )
        for key in ("invoke_planning_agent", "invoke_intelligence_agent"):
            fn = self.lambdas[key]
            fn.add_environment("MEMORY_ID", self.memory.attr_memory_id)
            fn.add_to_role_policy(memory_policy)

    # ------------------------------------------------------------------
    # AppSync real-time API (browser subscriptions to lifecycle events)
    # ------------------------------------------------------------------
    def _create_realtime_api(self) -> appsync.GraphqlApi:
        """Create an AppSync API that fans domain events to the browser.

        A publisher Lambda (invoked by an EventBridge rule) calls a
        ``publishShipmentEvent`` mutation; the Next.js cockpit subscribes to
        ``onShipmentEvent`` and renders lifecycle progress in real time. Auth is
        Cognito user pools (see :meth:`_create_api` for the shared pool).
        """
        # Cognito user pool shared by AppSync + API Gateway.
        self.user_pool = cognito.UserPool(
            self,
            "TmsUserPool",
            user_pool_name="tms-mvp-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.user_pool_client = self.user_pool.add_client(
            "TmsUserPoolClient",
            user_pool_client_name="tms-mvp-web",
            auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
        )

        api = appsync.GraphqlApi(
            self,
            "TmsRealtimeApi",
            name="tms-mvp-realtime",
            definition=appsync.Definition.from_schema(
                appsync.SchemaFile.from_asset(
                    os.path.join(self._project_root(), "appsync", "schema.graphql")
                )
            ),
            authorization_config=appsync.AuthorizationConfig(
                default_authorization=appsync.AuthorizationMode(
                    authorization_type=appsync.AuthorizationType.USER_POOL,
                    user_pool_config=appsync.UserPoolConfig(user_pool=self.user_pool),
                ),
                additional_authorization_modes=[
                    # IAM mode lets the publisher Lambda call the mutation.
                    appsync.AuthorizationMode(
                        authorization_type=appsync.AuthorizationType.IAM
                    )
                ],
            ),
        )

        # NONE data source + local resolvers: the mutation simply forwards its
        # arguments to subscribers; no backing store needed for a live feed.
        none_ds = api.add_none_data_source("NoneDataSource")
        none_ds.create_resolver(
            "PublishResolver",
            type_name="Mutation",
            field_name="publishShipmentEvent",
            request_mapping_template=appsync.MappingTemplate.from_string(
                '{"version":"2017-02-28","payload": $util.toJson($context.arguments)}'
            ),
            response_mapping_template=appsync.MappingTemplate.from_string(
                "$util.toJson($context.result)"
            ),
        )

        # Publisher Lambda: EventBridge rule -> Lambda -> AppSync mutation (IAM).
        publisher_fn = _lambda.Function(
            self,
            "RealtimePublisherFunction",
            function_name="tms-realtime-publisher",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/realtime_publisher/handler.handler",
            code=_lambda.Code.from_asset(self._project_root(), exclude=LAMBDA_ASSET_EXCLUDES),
            timeout=Duration.seconds(30),
            environment={
                "APPSYNC_API_URL": api.graphql_url,
                "APPSYNC_REGION": self.region,
            },
        )
        api.grant_mutation(publisher_fn, "publishShipmentEvent")

        rule = events.Rule(
            self,
            "TmsEventsToRealtimeRule",
            rule_name="tms-mvp-events-to-realtime",
            event_bus=self.event_bus,
            event_pattern=events.EventPattern(source=[EVENT_SOURCE]),
        )
        rule.add_target(targets.LambdaFunction(publisher_fn))

        CfnOutput(self, "RealtimeApiUrl", value=api.graphql_url,
                  description="AppSync GraphQL URL", export_name="TmsMvpRealtimeApiUrl")
        CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id,
                  export_name="TmsMvpUserPoolId")
        CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id,
                  export_name="TmsMvpUserPoolClientId")
        return api

    # ------------------------------------------------------------------
    # API Gateway + Orchestrator Lambda (HTTP ingress)
    # ------------------------------------------------------------------
    def _create_api(self) -> apigateway.RestApi:
        """Create the HTTP ingress: API Gateway → Orchestrator Lambda.

        The Orchestrator Lambda wraps ``agents.orchestrator_agent`` (classify +
        start the Step Functions workflow). API Gateway authorizes requests
        against the shared Cognito user pool.
        """
        orchestrator_fn = _lambda.Function(
            self,
            "InvokeOrchestratorFunction",
            function_name="tms-invoke-orchestrator",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambdas/invoke_orchestrator/handler.handler",
            code=_lambda.Code.from_asset(self._project_root(), exclude=LAMBDA_ASSET_EXCLUDES),
            timeout=Duration.seconds(60),
            layers=[self.agent_layer],
            environment={
                "TABLE_NAME": self.table.table_name,
                "EVENT_BUS_NAME": EVENT_BUS_NAME,
                "STATE_MACHINE_ARN": self.workflow.state_machine_arn,
                "MEMORY_ID": self.memory.attr_memory_id,
            },
        )
        self.table.grant_read_write_data(orchestrator_fn)
        self.workflow.grant_start_execution(orchestrator_fn)
        orchestrator_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["arn:aws:bedrock:*::foundation-model/*",
                           f"arn:aws:bedrock:*:{self.account}:inference-profile/*"],
            )
        )

        authorizer = apigateway.CognitoUserPoolsAuthorizer(
            self,
            "TmsApiAuthorizer",
            cognito_user_pools=[self.user_pool],
        )

        api = apigateway.RestApi(
            self,
            "TmsRestApi",
            rest_api_name="tms-mvp-api",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
            ),
        )
        shipments = api.root.add_resource("shipments")
        shipments.add_method(
            "POST",
            apigateway.LambdaIntegration(orchestrator_fn),
            authorizer=authorizer,
            authorization_type=apigateway.AuthorizationType.COGNITO,
        )

        CfnOutput(self, "ApiUrl", value=api.url,
                  description="API Gateway base URL", export_name="TmsMvpApiUrl")
        return api

    # ------------------------------------------------------------------
    # Athena / Glue over the S3 event archive
    # ------------------------------------------------------------------
    def _create_analytics(self) -> None:
        """Create a Glue database/table over the event archive + an Athena workgroup.

        Lets analysts query shipment history and SLM-vs-LLM usage directly from
        the archived events. QuickSight can be connected to these tables as an
        optional, documented step (not provisioned here).
        """
        results_bucket = s3.Bucket(
            self,
            "TmsAthenaResultsBucket",
            bucket_name=f"tms-mvp-athena-results-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        glue_db = glue.CfnDatabase(
            self,
            "TmsGlueDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(name="tms_mvp"),
        )

        glue.CfnTable(
            self,
            "TmsEventsGlueTable",
            catalog_id=self.account,
            database_name="tms_mvp",
            table_input=glue.CfnTable.TableInputProperty(
                name="domain_events",
                table_type="EXTERNAL_TABLE",
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name="dt", type="string"),
                ],
                parameters={"classification": "json"},
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=f"s3://{self.archive_bucket.bucket_name}/events/",
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.openx.data.jsonserde.JsonSerDe"
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name="event_type", type="string"),
                        glue.CfnTable.ColumnProperty(name="shipment_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="timestamp", type="string"),
                        glue.CfnTable.ColumnProperty(name="step", type="string"),
                        glue.CfnTable.ColumnProperty(name="model_tier", type="string"),
                        glue.CfnTable.ColumnProperty(name="confidence", type="double"),
                        glue.CfnTable.ColumnProperty(name="payload", type="string"),
                    ],
                ),
            ),
        )

        athena.CfnWorkGroup(
            self,
            "TmsAthenaWorkgroup",
            name="tms-mvp",
            recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{results_bucket.bucket_name}/results/"
                )
            ),
        )

        CfnOutput(self, "AthenaWorkgroup", value="tms-mvp",
                  description="Athena workgroup for TMS analytics")

    # ------------------------------------------------------------------
    # Frontend hosting: Next.js static export on S3 + CloudFront
    # ------------------------------------------------------------------
    def _create_frontend_hosting(self) -> None:
        """Host the Next.js cockpit on S3 + CloudFront, built at deploy time.

        The Next.js app is a static export (``output: export``). CDK builds it
        during ``cdk deploy`` (npm ci && npm run build) and uploads
        ``frontend/out`` to a private bucket fronted by CloudFront (OAI). A
        second deployment writes ``config.json`` with the real API Gateway URL,
        AppSync URL, and Cognito IDs so the static bundle can resolve endpoints
        at runtime (avoids baking deploy-time values into the build).
        """
        site_bucket = s3.Bucket(
            self,
            "TmsCockpitBucket",
            bucket_name=f"tms-mvp-cockpit-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        distribution = cloudfront.Distribution(
            self,
            "TmsCockpitDistribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                    site_bucket
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            error_responses=[
                # SPA-style fallback for client routes.
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                ),
            ],
        )

        frontend_dir = os.path.join(self._project_root(), "frontend")

        # Build the Next.js static export during `cdk deploy` inside the CDK
        # Node bundling container (Docker). The built site in frontend/out is
        # copied to the asset output and uploaded to the hosting bucket.
        site_asset = s3_deployment.Source.asset(
            frontend_dir,
            bundling=BundlingOptions(
                image=_lambda.Runtime.NODEJS_20_X.bundling_image,
                command=[
                    "bash",
                    "-c",
                    "npm ci && npm run build && cp -r out/* /asset-output/",
                ],
            ),
        )

        # Runtime config written from actual deploy-time resource values.
        config_source = s3_deployment.Source.json_data(
            "config.json",
            {
                "region": self.region,
                "apiUrl": self.rest_api.url,
                "realtimeApiUrl": self.realtime_api.graphql_url,
                "userPoolId": self.user_pool.user_pool_id,
                "userPoolClientId": self.user_pool_client.user_pool_client_id,
            },
        )

        s3_deployment.BucketDeployment(
            self,
            "TmsCockpitDeployment",
            sources=[site_asset, config_source],
            destination_bucket=site_bucket,
            distribution=distribution,
            distribution_paths=["/*"],
        )

        CfnOutput(
            self,
            "CockpitUrl",
            value=f"https://{distribution.distribution_domain_name}",
            description="Transportation Cockpit URL (CloudFront)",
            export_name="TmsMvpCockpitUrl",
        )

    def _create_dynamodb_table(self) -> dynamodb.Table:
        """Create the single-table DynamoDB resource.

        Key schema: PK (partition key, String), SK (sort key, String).
        Uses PAY_PER_REQUEST billing (demo, low traffic).
        Removal policy DESTROY so table is cleaned up on stack deletion.
        """
        table = dynamodb.Table(
            self,
            "TmsMvpTable",
            table_name="tms-mvp-table",
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )
        return table

    def _populate_seed_data(self) -> None:
        """Create a Custom Resource that populates DynamoDB with seed data on deploy.

        Uses a Lambda-backed Custom Resource. The Lambda writes 5 carriers,
        13 rates, and 1 sample shipment to the table during stack Create/Update.
        """
        # Lambda function for the Custom Resource
        seed_data_fn = _lambda.Function(
            self,
            "SeedDataFunction",
            function_name="tms-seed-data",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), "..", "lambdas", "seed_data")
            ),
            timeout=Duration.seconds(60),
            environment={
                "TABLE_NAME": self.table.table_name,
            },
        )

        # Grant the seed data Lambda read/write access to the table
        self.table.grant_read_write_data(seed_data_fn)

        # Custom Resource Provider using the Lambda function
        provider = cr.Provider(
            self,
            "SeedDataProvider",
            on_event_handler=seed_data_fn,
        )

        # Custom Resource that triggers seed data population on deploy
        CustomResource(
            self,
            "SeedDataResource",
            service_token=provider.service_token,
        )
