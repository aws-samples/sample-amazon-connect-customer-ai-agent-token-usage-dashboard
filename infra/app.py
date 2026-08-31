#!/usr/bin/env python3
"""CDK app entry point.

Deployment levels are additive:
  LEVEL_0 — Query_Library + dashboard widgets only (no compute, no storage)
  LEVEL_1 — + subscription filter, Lambda, EMF metrics, alarms, DynamoDB cache
  LEVEL_2 — + Firehose, S3, Glue, Athena views

No account, region, instance, or assistant identifier is hardcoded. All values
come from CDK context or the standard CDK_DEFAULT_ACCOUNT / CDK_DEFAULT_REGION
environment variables (populated automatically from your AWS credentials).

Configure via cdk.json context, -c flags, or environment variables:

  cdk deploy \\
    -c assistant_log_groups="/aws/wisdom/agent-a,/aws/wisdom/agent-b" \\
    -c connect_instance_ids="iid-1,iid-2"

Or edit the "context" block in cdk.json.
"""

import os

import aws_cdk as cdk

from stacks.ingestion import IngestionStack

app = cdk.App()


def _csv_context(key: str) -> list[str]:
    """Read a comma-separated context value into a list."""
    raw = app.node.try_get_context(key) or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


# Account and region resolve from CDK credentials by default; a context override
# is honored if supplied.
account = app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
region = app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION")

assistant_log_groups = _csv_context("assistant_log_groups")
connect_instance_ids = _csv_context("connect_instance_ids")

if not assistant_log_groups:
    raise SystemExit(
        "No assistant_log_groups configured. Set them in cdk.json context or pass "
        '-c assistant_log_groups="/aws/wisdom/your-assistant".'
    )
if not connect_instance_ids:
    raise SystemExit(
        "No connect_instance_ids configured. Set them in cdk.json context or pass "
        '-c connect_instance_ids="your-instance-id".'
    )

IngestionStack(
    app,
    "ConnectAITokenEfficiency",
    env=cdk.Environment(account=account, region=region),
    assistant_log_groups=assistant_log_groups,
    connect_instance_ids=connect_instance_ids,
)

app.synth()
