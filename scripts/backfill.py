#!/usr/bin/env python3
"""Backfill: replay existing assistant log events through the pipeline.

Reads all events from the three log groups, parses them, resolves channels,
and writes enriched rows to Firehose — exactly what the subscription filter does,
but for events that already existed before the filter was attached.

Usage:
    python scripts/backfill.py

Environment (set or inherit from the deployed Lambda):
    DELIVERY_STREAM_NAME, CHANNEL_CACHE_TABLE, CONNECT_INSTANCE_IDS
"""

import json
import os
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

import boto3

from connect_ai_tokens import constants as C
from connect_ai_tokens.channel import ChannelResolver, DynamoChannelCache
from connect_ai_tokens.config import Config, DeploymentLevel
from connect_ai_tokens.handler import pack_for_firehose, process
from connect_ai_tokens.metrics import MetricPublisher

REGION = "eu-west-2"
LOG_GROUPS = [
    "/aws/wisdom/tagalog-support",
    "/aws/wisdom/bet365-assistant-bet365-demo-v3-AssistantStack-QX913GJCE6MQ",
    "/aws/wisdom/AnyCompany-Fraud-Alerts-AgentAssist",
]
STREAM = os.environ.get(
    "DELIVERY_STREAM_NAME", "ConnectAITokenEfficiency-SpanStream-EXSsc2jXViNr"
)
CACHE_TABLE = os.environ.get(
    "CHANNEL_CACHE_TABLE",
    "ConnectAITokenEfficiency-ChannelCacheC9FE6E41-1E3348266JGGW",
)
INSTANCE_IDS = os.environ.get(
    "CONNECT_INSTANCE_IDS",
    "1de8296e-1e15-4af6-b842-9a5a38d9873c,"
    "cd54ca0b-aac4-42a5-9e6a-dd201aa135be,"
    "cf6475f7-06ea-424e-8ca0-a7c6a03dd6f6",
).split(",")


def fetch_all_events(logs_client) -> list[dict]:
    """Pull every event from all three log groups."""
    all_events = []
    for lg in LOG_GROUPS:
        print(f"  fetching {lg}...")
        paginator = logs_client.get_paginator("filter_log_events")
        for page in paginator.paginate(
            logGroupName=lg, startTime=0, interleaved=True
        ):
            for ev in page.get("events", []):
                all_events.append(ev)
    return all_events


def main() -> int:
    logs_client = boto3.client("logs", region_name=REGION)
    connect_client = boto3.client("connect", region_name=REGION)
    firehose_client = boto3.client("firehose", region_name=REGION)
    dynamo_client = boto3.client("dynamodb", region_name=REGION)

    print("Fetching existing events from assistant log groups...")
    raw_events = fetch_all_events(logs_client)
    print(f"  total events: {len(raw_events)}")

    # Filter to trace events only (same as the subscription filter pattern)
    trace_events = []
    for ev in raw_events:
        try:
            body = json.loads(ev.get("message", "{}"))
            if body.get("event_type") == C.EVENT_AI_AGENT_TRACE:
                trace_events.append(ev)
        except (ValueError, TypeError):
            pass
    print(f"  trace events: {len(trace_events)}")

    if not trace_events:
        print("Nothing to backfill.")
        return 0

    config = Config(
        deployment_level=DeploymentLevel.LEVEL_2,
        connect_instance_ids=tuple(INSTANCE_IDS),
        channel_cache_table=CACHE_TABLE,
        delivery_stream_name=STREAM,
    )

    cache = DynamoChannelCache(CACHE_TABLE, dynamo_client)
    resolver = ChannelResolver(connect_client, cache=cache)
    publisher = MetricPublisher(config.metric_namespace)

    print("Processing...")
    result, rows = process(trace_events, config, resolver, publisher)
    print(f"  parsed: {result.spans_parsed}, failures: {result.parse_failures}")
    print(f"  store rows: {len(rows)}")
    print(f"  unresolved channels: {result.unresolved_channels}")
    print(f"  spans without identifiers: {result.spans_without_identifiers}")

    if not rows:
        print("No rows to write.")
        return 0

    packed = pack_for_firehose(rows)
    print(f"  firehose records (packed): {len(packed)}")

    for i in range(0, len(packed), 500):
        batch = packed[i : i + 500]
        resp = firehose_client.put_record_batch(
            DeliveryStreamName=STREAM,
            Records=[{"Data": d} for d in batch],
        )
        failed = resp.get("FailedPutCount", 0)
        if failed:
            print(f"  WARNING: {failed} records failed in batch {i // 500}")
        time.sleep(0.5)

    print(f"\nBackfill complete. {len(packed)} records sent to Firehose.")
    print(f"Data will be queryable in Athena after the next Firehose flush (~5 min).")
    print(f"  Table: connect_ai_token_efficiency.spans")
    print(f"  Bucket: s3://{os.environ.get('SPAN_STORE_BUCKET', 'connectaitokenefficiency-spanstore99fe5a59-ztuuckhwcoch')}/spans/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
