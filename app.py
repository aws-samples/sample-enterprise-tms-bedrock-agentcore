#!/usr/bin/env python3
"""TMS Demo — CDK Application entry point.

Deploys the full TMS stack with a single `cdk deploy` command.
Uses the default AWS account/region from the CDK CLI environment.

cdk-nag (AwsSolutionsChecks) runs as an aspect on every synth to validate the
stack against AWS security and best-practice rules. Findings that are
intentional for a deployable *sample* (rather than a hardened production
system) are suppressed with explicit justifications in cdk/nag_suppressions.py.
"""

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from cdk.tms_mvp_stack import TmsMvpStack
from cdk.nag_suppressions import apply_nag_suppressions

app = cdk.App()

stack = TmsMvpStack(
    app,
    "TmsMvpStack",
    description="TMS Demo — AgentCore Architecture with Step Functions backbone",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
)

# Apply documented suppressions for intentional sample-level choices, then
# run the AWS Solutions checks across the whole app.
apply_nag_suppressions(stack)
cdk.Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

app.synth()
