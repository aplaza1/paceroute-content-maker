#!/usr/bin/env python3
import os
import aws_cdk as cdk
from stacks.pipeline_stack import PipelineStack

app = cdk.App()

PipelineStack(
    app,
    "PaceroutePipelineStack",
    env=cdk.Environment(
        account=os.environ["CDK_DEFAULT_ACCOUNT"],
        region=os.environ["CDK_DEFAULT_REGION"],
    ),
)

app.synth()
