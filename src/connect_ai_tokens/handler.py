"""CloudWatch Logs subscription handler — the Level 1/2 ingest path.

Flow per invocation:

    gzip payload -> Assistant_Log events -> Span_Parser -> Channel_Resolver
        -> reconcile + apportion -> Metric_Publisher (EMF to stdout)
        -> Firehose batch (Level 2 only)

Two behaviours worth stating because they are easy to get wrong:

**One bad record never fails the batch.** A parse failure is counted, located, and
skipped. Raising would make CloudWatch retry the whole batch, re-emitting metrics
for every record that already succeeded.

**Spans are packed before Firehose.** Firehose bills per GB in 5 KB increments, so
a single ~2 KB span record is billed as 5 KB — a 2.5x penalty. Records are packed
to approach 1 MB instead.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from . import constants as C
from .channel import ChannelInfo, ChannelResolver, DynamoChannelCache
from .config import Config
from .metrics import MetricPublisher, derive_facts
from .span_parser import SpanParseError, parse_span

FIREHOSE_TARGET_BYTES = 900_000
"""Just under the 1 MB record limit, leaving room for the newline framing."""


@dataclass
class IngestResult:
    events_seen: int = 0
    trace_events: int = 0
    spans_parsed: int = 0
    parse_failures: int = 0
    unknown_fields: set[str] = field(default_factory=set)
    emf_records: int = 0
    firehose_records: int = 0
    unresolved_channels: int = 0
    """DescribeContact was attempted and failed. An API or permissions problem."""

    spans_without_identifiers: int = 0
    """The span carried no contact id or instance ARN, so no call was possible.

    Counted separately from unresolved_channels because the remediation differs:
    this is a data-shape observation about the span, not a failure to reach
    Connect. Both land in the UNRESOLVED channel group, but conflating the counts
    would send someone debugging IAM when nothing is broken.
    """

    def to_log(self) -> dict:
        return {
            "message": "ingest_summary",
            "events_seen": self.events_seen,
            "trace_events": self.trace_events,
            "spans_parsed": self.spans_parsed,
            "parse_failures": self.parse_failures,
            "parse_failure_rate": round(
                self.parse_failures / self.trace_events, 6
            )
            if self.trace_events
            else 0.0,
            # Surfaced so a service-side schema addition is visible rather than
            # silently absorbed (Requirement 2.12).
            "unknown_span_fields": sorted(self.unknown_fields),
            "emf_records": self.emf_records,
            "firehose_records": self.firehose_records,
            "unresolved_channels": self.unresolved_channels,
            "spans_without_identifiers": self.spans_without_identifiers,
        }


def decode_payload(event: dict) -> list[dict]:
    """Decode a CloudWatch Logs subscription payload into log events."""
    raw = event.get("awslogs", {}).get("data")
    if not raw:
        return []
    payload = json.loads(gzip.decompress(base64.b64decode(raw)))
    if payload.get("messageType") == "CONTROL_MESSAGE":
        return []
    return payload.get("logEvents", [])


def _instance_id_from_span(span) -> str | None:
    arn = span.text("instance_arn")
    return arn.rsplit("/", 1)[-1] if arn else None


def process(
    log_events: list[dict],
    config: Config,
    resolver: ChannelResolver | None,
    publisher: MetricPublisher,
) -> tuple[IngestResult, list[dict]]:
    """Parse, enrich and aggregate. Returns the result plus rows for the store."""
    result = IngestResult()
    rows: list[dict] = []

    for log_event in log_events:
        result.events_seen += 1
        try:
            body = json.loads(log_event.get("message", "{}"))
        except (ValueError, TypeError):
            continue

        if body.get("event_type") != C.EVENT_AI_AGENT_TRACE:
            continue
        result.trace_events += 1

        try:
            span = parse_span(body.get("span"))
        except SpanParseError as exc:
            result.parse_failures += 1
            print(
                json.dumps(
                    {
                        "message": "span_parse_failure",
                        "reason": str(exc),
                        "log_stream": log_event.get("logStreamName"),
                        "log_event_timestamp": log_event.get("timestamp"),
                    }
                )
            )
            continue

        result.spans_parsed += 1
        result.unknown_fields.update(span.unknown_fields.keys())

        contact_id = span.text("initial_contact_id") or span.text("contact_id")
        instance_id = _instance_id_from_span(span)
        if resolver and contact_id and instance_id:
            info = resolver.resolve(contact_id, instance_id)
            if not info.resolved:
                result.unresolved_channels += 1
        else:
            info = ChannelInfo.unresolved(contact_id or "unknown")
            result.spans_without_identifiers += 1

        facts = derive_facts(span, info)
        publisher.add(facts)

        if config.persists_spans:
            rows.append(_to_row(facts))

    return result, rows


def _to_row(facts) -> dict:
    """Flatten to the Span_Store schema, carrying every documented join key."""
    span = facts.span
    r = facts.reasoning
    day = None
    start = span.int_or_none("start_timestamp")
    if start:
        day = time.strftime("%Y-%m-%d", time.gmtime(start / 1000))

    return {
        "span_id": span.text("span_id"),
        "parent_span_id": span.text("parent_span_id"),
        "span_name": span.span_name,
        "status": span.text("status"),
        "error_type": span.text("error_type"),
        "start_timestamp": start,
        "end_timestamp": span.int_or_none("end_timestamp"),
        "duration_ms": span.duration_ms,
        "usage_input_tokens": span.int_or_none("usage_input_tokens"),
        "usage_output_tokens": span.int_or_none("usage_output_tokens"),
        "usage_total_tokens": span.int_or_none("usage_total_tokens"),
        # Null rather than 0 so absence survives into the store and Cache_State
        # stays derivable in SQL.
        "cache_read_input_tokens": span.int_or_none("cache_read_input_tokens"),
        "cache_write_input_tokens": span.int_or_none("cache_write_input_tokens"),
        "has_cache_fields": span.has_any_cache_field,
        "reconciliation_state": facts.reconciliation.value,
        "time_to_first_token_ms": span.int_or_none("time_to_first_token_ms"),
        "request_model": span.text("request_model"),
        "request_max_tokens": span.int_or_none("request_max_tokens"),
        "response_finish_reasons": span.text("response_finish_reasons"),
        "system_instructions_chars": len(span.text("system_instructions") or "")
        or None,
        "chars_text": r.chars_text if r else None,
        "chars_reasoning": r.chars_reasoning if r else None,
        "chars_tool": r.chars_tool if r else None,
        "tokens_reasoning": round(r.tokens_reasoning, 4) if r else None,
        "tokens_text": round(r.tokens_text, 4) if r else None,
        "tokens_tool": round(r.tokens_tool, 4) if r else None,
        # join keys
        "ai_agent_id": span.text("ai_agent_id"),
        "ai_agent_name": span.text("ai_agent_name"),
        "ai_agent_version": span.text("ai_agent_version"),
        "ai_agent_type": span.text("ai_agent_type"),
        "ai_agent_orchestrator_use_case": span.text("ai_agent_orchestrator_use_case"),
        "prompt_id": span.text("prompt_id"),
        "prompt_version": span.text("prompt_version"),
        "assistant_id": facts.channel_info.assistant_id,
        "session_id": facts.channel_info.session_id,
        "contact_id": span.text("contact_id"),
        "initial_contact_id": span.text("initial_contact_id"),
        "instance_id": _instance_id_from_span(span),
        "channel": facts.channel_info.channel,
        "escalated": facts.channel_info.escalated,
        "dt": day,
    }


def pack_for_firehose(rows: list[dict]) -> list[bytes]:
    """Pack newline-delimited rows into ~1 MB records.

    One row per record would be billed at the 5 KB minimum increment, so packing
    is a cost decision, not a throughput one.
    """
    records: list[bytes] = []
    buf: list[str] = []
    size = 0
    for row in rows:
        line = json.dumps(row, separators=(",", ":"), default=str)
        if size + len(line) + 1 > FIREHOSE_TARGET_BYTES and buf:
            records.append(("\n".join(buf) + "\n").encode())
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        records.append(("\n".join(buf) + "\n").encode())
    return records


def handler(event: dict, context: Any = None) -> dict:  # pragma: no cover
    config = Config.from_env()
    log_events = decode_payload(event)

    resolver = None
    if config.connect_instance_ids or True:
        import boto3

        cache = None
        if config.channel_cache_table:
            cache = DynamoChannelCache(
                config.channel_cache_table, boto3.client("dynamodb")
            )
        resolver = ChannelResolver(
            boto3.client("connect"),
            cache=cache,
            max_attempts=config.describe_contact_max_attempts,
        )

    publisher = MetricPublisher(config.metric_namespace)
    result, rows = process(log_events, config, resolver, publisher)

    now_ms = int(time.time() * 1000)
    for line in publisher.serialise(now_ms):
        print(line)
        result.emf_records += 1

    if config.persists_spans and rows and config.delivery_stream_name:
        import boto3

        firehose = boto3.client("firehose")
        packed = pack_for_firehose(rows)
        for i in range(0, len(packed), 500):
            firehose.put_record_batch(
                DeliveryStreamName=config.delivery_stream_name,
                Records=[{"Data": d} for d in packed[i : i + 500]],
            )
        result.firehose_records = len(packed)

    print(json.dumps(result.to_log()))
    return result.to_log()
