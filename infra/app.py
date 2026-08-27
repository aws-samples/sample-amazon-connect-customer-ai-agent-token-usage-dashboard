#!/usr/bin/env python3
"""CDK app entry point.

Deployment levels are additive:
  LEVEL_0 — Query_Library + dashboard widgets only (no compute, no storage)
  LEVEL_1 — + subscription filter, Lambda, EMF metrics, alarms, DynamoDB cache
  LEVEL_2 — + Firehose, S3, Glue, Athena views
"""

import aws_cdk as cdk

from stacks.ingestion import IngestionStack

app = cdk.App()

env = cdk.Environment(account="101506645078", region="eu-west-2")

IngestionStack(
    app,
    "ConnectAITokenEfficiency",
    env=env,
    assistant_log_groups=[
        "/aws/wisdom/tagalog-support",
        "/aws/wisdom/bet365-assistant-bet365-demo-v3-AssistantStack-QX913GJCE6MQ",
        "/aws/wisdom/AnyCompany-Fraud-Alerts-AgentAssist",
    ],
    connect_instance_ids=[
        "1de8296e-1e15-4af6-b842-9a5a38d9873c",
        "cd54ca0b-aac4-42a5-9e6a-dd201aa135be",
        "cf6475f7-06ea-424e-8ca0-a7c6a03dd6f6",
    ],
)

app.synth()
