"""cdk-nag suppressions for intentional sample-level choices.

This project runs cdk-nag's ``AwsSolutionsChecks`` on every synth. The checks
below are suppressed with explicit justifications because they reflect
deliberate decisions appropriate for a deployable *sample* / reference
implementation rather than a hardened production deployment.

Each suppression documents WHY it is acceptable here and, where relevant, what
a production deployment should do instead. This keeps the security posture
transparent for anyone adapting the sample.
"""

from aws_cdk import Stack
from cdk_nag import NagSuppressions


def apply_nag_suppressions(stack: Stack) -> None:
    """Apply stack-level cdk-nag suppressions with justifications."""

    NagSuppressions.add_stack_suppressions(
        stack,
        [
            {
                "id": "AwsSolutions-IAM4",
                "reason": (
                    "Lambda execution roles use the AWS managed policy "
                    "AWSLambdaBasicExecutionRole for CloudWatch Logs access. This "
                    "is the standard, least-privilege logging policy for a sample; "
                    "a production deployment may replace it with a scoped customer "
                    "managed policy."
                ),
            },
            {
                "id": "AwsSolutions-IAM5",
                "reason": (
                    "Wildcards are limited to (a) Amazon Bedrock foundation-model "
                    "and cross-region inference-profile ARNs, which cannot be "
                    "enumerated to a single ARN because the SLM-first/LLM-fallback "
                    "pattern routes across multiple models/regions, and (b) CDK "
                    "auto-generated grants (BucketDeployment, log streams). No "
                    "wildcard grants administrative or account-wide access."
                ),
            },
            {
                "id": "AwsSolutions-L1",
                "reason": (
                    "Lambda functions pin the Python 3.12 runtime explicitly for "
                    "reproducibility of the sample. Consumers upgrade the runtime "
                    "as newer versions become the recommended default."
                ),
            },
            {
                "id": "AwsSolutions-SF1",
                "reason": (
                    "The Step Functions state machine logs execution outcomes via "
                    "the domain-event archive (S3) and per-Lambda CloudWatch logs. "
                    "Full ALL-level SF execution logging is optional for a demo "
                    "workflow and can be enabled by consumers."
                ),
            },
            {
                "id": "AwsSolutions-SF2",
                "reason": (
                    "X-Ray tracing is optional for this sample's single linear "
                    "workflow; AgentCore Observability and per-Lambda logs provide "
                    "the traceability the demo needs."
                ),
            },
            {
                "id": "AwsSolutions-SQS3",
                "reason": (
                    "The progress queue already has a dead-letter queue. The DLQ "
                    "itself does not need its own DLQ."
                ),
            },
            {
                "id": "AwsSolutions-SQS4",
                "reason": (
                    "Queues are reached only by same-account AWS principals over "
                    "the AWS network (EventBridge rule target, Lambda). SSL is "
                    "enforced on the buckets; SQS enforce-SSL is optional for this "
                    "in-account demo flow."
                ),
            },
            {
                "id": "AwsSolutions-S1",
                "reason": (
                    "S3 server access logging is omitted for the demo buckets to "
                    "keep the sample footprint small. Consumers enable access "
                    "logging per their audit requirements; all buckets already "
                    "block public access and enforce SSL."
                ),
            },
            {
                "id": "AwsSolutions-S10",
                "reason": (
                    "All buckets set enforce_ssl=True (aws:SecureTransport). Any "
                    "flag here is on a CDK-managed asset/deployment bucket."
                ),
            },
            {
                "id": "AwsSolutions-APIG2",
                "reason": (
                    "Request validation is handled in the Orchestrator Lambda, "
                    "which validates the shipment payload fields before starting "
                    "the workflow and returns 400 on invalid input."
                ),
            },
            {
                "id": "AwsSolutions-APIG1",
                "reason": (
                    "API Gateway access logging is optional for this sample; the "
                    "Orchestrator Lambda emits structured logs for every request. "
                    "Consumers enable stage access logs as needed."
                ),
            },
            {
                "id": "AwsSolutions-APIG3",
                "reason": (
                    "AWS WAF is not attached in the sample. The API requires "
                    "Cognito user-pool authorization; consumers add WAF for "
                    "production internet-facing exposure."
                ),
            },
            {
                "id": "AwsSolutions-APIG4",
                "reason": (
                    "The POST /shipments method is protected by a Cognito user "
                    "pool authorizer. CORS preflight (OPTIONS) is intentionally "
                    "unauthenticated as required by browsers."
                ),
            },
            {
                "id": "AwsSolutions-APIG6",
                "reason": (
                    "CloudWatch execution logging per method is optional for the "
                    "demo; the backing Lambda provides structured request logs."
                ),
            },
            {
                "id": "AwsSolutions-COG2",
                "reason": (
                    "MFA is not enforced on the demo Cognito user pool to keep the "
                    "sample's sign-in flow simple. Production deployments should "
                    "enable MFA."
                ),
            },
            {
                "id": "AwsSolutions-COG3",
                "reason": (
                    "Advanced security mode (AdvancedSecurityMode) is left at the "
                    "default for the sample user pool; consumers enable it for "
                    "production threat protection."
                ),
            },
            {
                "id": "AwsSolutions-CFR1",
                "reason": (
                    "Geo restriction is not applied; the cockpit sample is meant "
                    "to be reachable for demonstration. Consumers add geo "
                    "restrictions per their requirements."
                ),
            },
            {
                "id": "AwsSolutions-CFR2",
                "reason": (
                    "AWS WAF is not attached to the CloudFront distribution in the "
                    "sample. Consumers add WAF for production internet-facing use."
                ),
            },
            {
                "id": "AwsSolutions-CFR3",
                "reason": (
                    "CloudFront access logging is omitted for the demo "
                    "distribution to keep the footprint small; consumers enable "
                    "it per their audit needs."
                ),
            },
            {
                "id": "AwsSolutions-CFR4",
                "reason": (
                    "The distribution uses the default CloudFront viewer "
                    "certificate (TLS). A production deployment with a custom "
                    "domain configures a minimum TLS 1.2 policy on its ACM cert."
                ),
            },
            {
                "id": "AwsSolutions-CFR7",
                "reason": (
                    "The S3 origin uses Origin Access Control (OAC) via "
                    "S3BucketOrigin.with_origin_access_control."
                ),
            },
            {
                "id": "AwsSolutions-DDB3",
                "reason": (
                    "Point-in-time recovery is not enabled on the demo table "
                    "(seed data is recreated on deploy). Consumers enable PITR for "
                    "production data."
                ),
            },
        ],
    )
