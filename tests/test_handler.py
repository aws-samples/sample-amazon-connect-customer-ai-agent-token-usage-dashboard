"""End-to-end ingest tests: subscription payload through to store rows."""

from __future__ import annotations

import base64
import gzip
import json

import pytest

from connect_ai_tokens.channel import ChannelInfo, ChannelResolver
from connect_ai_tokens.config import Config, DeploymentLevel
from connect_ai_tokens.handler import (
    FIREHOSE_TARGET_BYTES,
    decode_payload,
    pack_for_firehose,
    process,
)
from connect_ai_tokens.metrics import MetricPublisher

REAL_ARN = (
    "arn:aws:wisdom:eu-west-2:101506645078:session/"
    "1522ee99-f6f3-4641-ad8e-89f7348c577d/17395249-611b-4056-abd9-cfa53ce64f87"
)


class FakeConnect:
    def __init__(self, channel="VOICE"):
        self.channel = channel
        self.calls = 0

    def describe_contact(self, InstanceId, ContactId):  # noqa: N803
        self.calls += 1
        return {
            "Contact": {
                "Channel": self.channel,
                "WisdomInfo": {
                    "SessionArn": REAL_ARN,
                    "AiAgents": [{"AiAgentEscalated": False}],
                },
            }
        }


def subscription_payload(events: list[dict]) -> dict:
    body = {
        "messageType": "DATA_MESSAGE",
        "logGroup": "/aws/wisdom/test",
        "logStream": "QiCAssistantTranscript",
        "logEvents": [
            {"id": str(i), "timestamp": 1700000000000 + i, "message": json.dumps(e)}
            for i, e in enumerate(events)
        ],
    }
    raw = gzip.compress(json.dumps(body).encode())
    return {"awslogs": {"data": base64.b64encode(raw).decode()}}


def level2() -> Config:
    return Config(deployment_level=DeploymentLevel.LEVEL_2)


# --------------------------------------------------------------------------


def test_decode_payload_roundtrip():
    ev = {"event_type": "TRANSCRIPT_AI_AGENT_TRACE", "span": "{span_name=inference}"}
    got = decode_payload(subscription_payload([ev]))
    assert len(got) == 1
    assert json.loads(got[0]["message"])["event_type"] == "TRANSCRIPT_AI_AGENT_TRACE"


def test_control_message_ignored():
    raw = gzip.compress(json.dumps({"messageType": "CONTROL_MESSAGE"}).encode())
    assert decode_payload({"awslogs": {"data": base64.b64encode(raw).decode()}}) == []


def test_one_bad_record_does_not_fail_the_batch():
    """CloudWatch would retry the whole batch, re-emitting good metrics."""
    good = {
        "event_type": "TRANSCRIPT_AI_AGENT_TRACE",
        "span": (
            "{span_name=inference, usage_input_tokens=90, usage_output_tokens=10, "
            "usage_total_tokens=100}"
        ),
    }
    bad = {"event_type": "TRANSCRIPT_AI_AGENT_TRACE", "span": "not-a-span"}
    events = decode_payload(subscription_payload([good, bad, good]))

    pub = MetricPublisher()
    result, _ = process(events, level2(), None, pub)

    assert result.trace_events == 3
    assert result.spans_parsed == 2
    assert result.parse_failures == 1
    assert pub.bucket_count > 0


def test_non_trace_events_skipped():
    events = decode_payload(
        subscription_payload(
            [
                {"event_type": "TRANSCRIPT_UTTERANCE", "utterance": "hi"},
                {"event_type": "TRANSCRIPT_CREATE_SESSION"},
            ]
        )
    )
    result, rows = process(events, level2(), None, MetricPublisher())
    assert result.events_seen == 2
    assert result.trace_events == 0
    assert rows == []


def test_channel_resolved_once_per_contact():
    span = (
        "{span_name=inference, initial_contact_id=c1, "
        "instance_arn=arn:aws:connect:eu-west-2:1:instance/i1, "
        "usage_input_tokens=90, usage_output_tokens=10, usage_total_tokens=100}"
    )
    events = decode_payload(
        subscription_payload(
            [{"event_type": "TRANSCRIPT_AI_AGENT_TRACE", "span": span}] * 5
        )
    )
    connect = FakeConnect()
    resolver = ChannelResolver(connect, sleep=lambda _: None)
    result, rows = process(events, level2(), resolver, MetricPublisher())

    assert result.spans_parsed == 5
    assert connect.calls == 1
    assert all(r["channel"] == "VOICE" for r in rows)


def test_store_row_preserves_absence_as_null_not_zero():
    """If absence became 0 in the store, Cache_State would be undecidable in SQL."""
    span = (
        "{span_name=inference, initial_contact_id=c1, usage_input_tokens=90, "
        "usage_output_tokens=10, usage_total_tokens=100}"
    )
    events = decode_payload(
        subscription_payload([{"event_type": "TRANSCRIPT_AI_AGENT_TRACE", "span": span}])
    )
    _, rows = process(events, level2(), None, MetricPublisher())
    assert rows[0]["cache_read_input_tokens"] is None
    assert rows[0]["cache_write_input_tokens"] is None
    assert rows[0]["has_cache_fields"] is False


def test_store_row_carries_every_join_key():
    span = (
        "{span_name=inference, span_id=s1, initial_contact_id=c1, contact_id=c1, "
        "instance_arn=arn:aws:connect:eu-west-2:1:instance/i1, "
        "ai_agent_name=A, ai_agent_version=3, prompt_id=p1, prompt_version=2, "
        "request_model=m1, usage_input_tokens=90, usage_output_tokens=10, "
        "usage_total_tokens=100, start_timestamp=1786003001631, "
        "end_timestamp=1786003003000}"
    )
    events = decode_payload(
        subscription_payload([{"event_type": "TRANSCRIPT_AI_AGENT_TRACE", "span": span}])
    )
    connect = FakeConnect()
    _, rows = process(
        events, level2(), ChannelResolver(connect, sleep=lambda _: None), MetricPublisher()
    )
    row = rows[0]
    for key in (
        "contact_id",
        "initial_contact_id",
        "session_id",
        "assistant_id",
        "instance_id",
        "ai_agent_name",
        "ai_agent_version",
        "prompt_id",
        "prompt_version",
        "request_model",
        "channel",
        "dt",
    ):
        assert row[key] is not None, f"join key {key} missing"
    assert row["dt"] == "2026-08-06"
    assert row["duration_ms"] == 1369


def test_level1_does_not_produce_store_rows():
    span = "{span_name=inference, usage_total_tokens=100}"
    events = decode_payload(
        subscription_payload([{"event_type": "TRANSCRIPT_AI_AGENT_TRACE", "span": span}])
    )
    cfg = Config(deployment_level=DeploymentLevel.LEVEL_1)
    _, rows = process(events, cfg, None, MetricPublisher())
    assert rows == []


def test_unknown_fields_surfaced_in_summary():
    span = "{span_name=inference, usage_total_tokens=100, brand_new_thing=7}"
    events = decode_payload(
        subscription_payload([{"event_type": "TRANSCRIPT_AI_AGENT_TRACE", "span": span}])
    )
    result, _ = process(events, level2(), None, MetricPublisher())
    assert "brand_new_thing" in result.to_log()["unknown_span_fields"]


# --------------------------------------------------------------------------
# Firehose packing - a cost decision
# --------------------------------------------------------------------------


def test_packing_batches_many_rows_into_few_records():
    rows = [{"span_id": f"s{i}", "usage_total_tokens": i} for i in range(5000)]
    packed = pack_for_firehose(rows)
    assert len(packed) < len(rows) / 100, "packing should collapse rows aggressively"
    assert all(len(p) <= FIREHOSE_TARGET_BYTES + 200 for p in packed)


def test_packing_preserves_every_row():
    rows = [{"span_id": f"s{i}"} for i in range(1234)]
    packed = pack_for_firehose(rows)
    recovered = [
        json.loads(line)
        for rec in packed
        for line in rec.decode().splitlines()
        if line
    ]
    assert len(recovered) == 1234
    assert {r["span_id"] for r in recovered} == {f"s{i}" for i in range(1234)}


def test_packing_empty_input():
    assert pack_for_firehose([]) == []


# --------------------------------------------------------------------------
# Golden - the real corpus through the real pipeline
# --------------------------------------------------------------------------


@pytest.mark.golden
def test_full_corpus_through_pipeline(events, manifest):
    payload = subscription_payload(events)
    log_events = decode_payload(payload)

    pub = MetricPublisher()
    connect = FakeConnect()
    resolver = ChannelResolver(connect, sleep=lambda _: None)
    result, rows = process(log_events, level2(), resolver, pub)

    assert result.events_seen == manifest["corpus"]["events"]
    assert result.trace_events == manifest["corpus"]["trace_events"]
    assert result.spans_parsed == manifest["corpus"]["spans_parsed"]
    assert result.parse_failures == 0
    assert result.unknown_fields == set()

    inference_rows = [
        r for r in rows if r["span_name"] == "inference" and r["usage_total_tokens"]
    ]
    assert len(inference_rows) == manifest["corpus"]["token_bearing_inference_spans"]
    assert sum(r["usage_total_tokens"] for r in inference_rows) == manifest["tokens"][
        "total"
    ]

    # Every row must be partitionable.
    assert all(r["dt"] for r in rows)

    # Channel is VOICE where the span carried the identifiers needed to resolve
    # it, and UNRESOLVED otherwise. UNRESOLVED must never be silently folded in
    # with a real channel - it is its own group.
    assert {r["channel"] for r in rows} <= {"VOICE", "UNRESOLVED"}
    for r in rows:
        resolvable = bool(r["initial_contact_id"] and r["instance_id"])
        expected = "VOICE" if resolvable else "UNRESOLVED"
        assert r["channel"] == expected, (
            f"span {r['span_id']} resolvable={resolvable} but channel={r['channel']}"
        )

    unresolved = [r for r in rows if r["channel"] == "UNRESOLVED"]
    assert unresolved, "corpus is expected to contain spans without a contact id"

    # The two causes are counted separately: nothing was broken here, the spans
    # simply carried no identifiers, so no DescribeContact call was possible.
    assert result.unresolved_channels == 0
    assert result.spans_without_identifiers == len(unresolved)


@pytest.mark.golden
def test_corpus_emf_is_serialisable(events):
    log_events = decode_payload(subscription_payload(events))
    pub = MetricPublisher()
    process(log_events, level2(), None, pub)
    lines = pub.serialise(1700000000000)
    assert lines
    for line in lines:
        rec = json.loads(line)
        assert rec["_aws"]["CloudWatchMetrics"][0]["Dimensions"]
        assert "channel" in rec
