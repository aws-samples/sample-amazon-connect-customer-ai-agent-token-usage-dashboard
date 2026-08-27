"""Metric_Publisher tests, including the cardinality bound."""

from __future__ import annotations

import json

import pytest

from connect_ai_tokens.channel import ChannelInfo
from connect_ai_tokens.metrics import (
    DIMENSION_SETS,
    CacheState,
    MetricPublisher,
    cache_state_by_agent,
    derive_cache_state,
    derive_facts,
)
from connect_ai_tokens.span_parser import parse_span


def span(**kw):
    body = ", ".join(f"{k}={v}" for k, v in kw.items())
    return parse_span("{span_name=inference, " + body + "}")


def voice(cid="c1"):
    return ChannelInfo(contact_id=cid, channel="VOICE", resolved=True)


# --------------------------------------------------------------------------
# Cache_State from presence
# --------------------------------------------------------------------------


def test_cache_state_off_when_fields_absent():
    spans = [span(usage_input_tokens=100, usage_total_tokens=100) for _ in range(3)]
    assert derive_cache_state(spans) is CacheState.OFF


def test_cache_state_on_when_all_spans_report_cache():
    spans = [
        span(usage_input_tokens=100, cache_read_input_tokens=50, usage_total_tokens=150)
        for _ in range(3)
    ]
    assert derive_cache_state(spans) is CacheState.ON


def test_cache_state_on_even_when_cache_values_are_zero():
    """Presence, not value. Zero hits with caching active is still ON."""
    spans = [
        span(usage_input_tokens=100, cache_read_input_tokens=0, usage_total_tokens=100)
    ]
    assert derive_cache_state(spans) is CacheState.ON


def test_cache_state_mixed():
    spans = [
        span(usage_input_tokens=100, cache_read_input_tokens=50, usage_total_tokens=150),
        span(usage_input_tokens=100, usage_total_tokens=100),
    ]
    assert derive_cache_state(spans) is CacheState.MIXED


def test_cache_state_no_data_when_no_inference_spans():
    """Distinct from OFF - nothing was observed, so nothing is claimed."""
    assert derive_cache_state([]) is CacheState.NO_DATA
    assert derive_cache_state([parse_span("{span_name=invoke_agent}")]) is CacheState.NO_DATA


def test_cache_state_keyed_by_agent_and_version():
    spans = [
        span(ai_agent_name="A", ai_agent_version=1, usage_total_tokens=10),
        span(
            ai_agent_name="A",
            ai_agent_version=2,
            cache_read_input_tokens=5,
            usage_total_tokens=10,
        ),
    ]
    states = cache_state_by_agent(spans)
    assert states[("A", "1")] is CacheState.OFF
    assert states[("A", "2")] is CacheState.ON


@pytest.mark.golden
def test_cache_state_matches_manifest(inference_spans, manifest):
    states = cache_state_by_agent(inference_spans)
    by_name: dict[str, set] = {}
    for (name, _version), state in states.items():
        by_name.setdefault(name, set()).add(state.value)
    for agent, expected in manifest["cache"]["state_by_agent"].items():
        assert expected["state"] in by_name[agent]


# --------------------------------------------------------------------------
# Cardinality
# --------------------------------------------------------------------------


def test_five_dimension_sets_all_channel_scoped():
    assert len(DIMENSION_SETS) == 5
    for dims in DIMENSION_SETS:
        assert "channel" in dims, f"{dims} would allow a blended metric"


def test_bucket_count_is_linear_not_multiplicative():
    """The whole point: five buckets per distinct combination, not a cross product."""
    pub = MetricPublisher()
    agents = [f"agent{i}" for i in range(20)]
    models = [f"model{i}" for i in range(4)]
    for i, agent in enumerate(agents):
        for model in models:
            s = span(
                ai_agent_name=agent,
                ai_agent_version=1,
                prompt_version=1,
                request_model=model,
                instance_arn=f"arn:aws:connect:eu-west-2:1:instance/inst{i % 3}",
                usage_total_tokens=100,
                usage_output_tokens=10,
                usage_input_tokens=90,
            )
            pub.add(derive_facts(s, voice()))

    cross_product = 20 * 4 * 3  # agents x models x instances, before versions
    assert pub.bucket_count < cross_product, (
        f"{pub.bucket_count} buckets should stay below the {cross_product} cross "
        "product; the fixed dimension sets are the cost control"
    )


def test_emf_record_shape():
    pub = MetricPublisher(namespace="Test/NS")
    pub.add(
        derive_facts(
            span(usage_input_tokens=90, usage_output_tokens=10, usage_total_tokens=100),
            voice(),
        )
    )
    lines = pub.serialise(timestamp_ms=1700000000000)
    assert lines
    rec = json.loads(lines[0])
    assert rec["_aws"]["Timestamp"] == 1700000000000
    assert rec["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "Test/NS"
    assert rec["channel"] == "VOICE"
    assert rec["TotalTokens"] == 100


# --------------------------------------------------------------------------
# Bucket derivations
# --------------------------------------------------------------------------


def test_barge_in_tokens_counted_as_discarded():
    pub = MetricPublisher()
    s = parse_span(
        "{span_name=inference, status=ERROR, error_type=barge_in, "
        "usage_total_tokens=500, usage_input_tokens=480, usage_output_tokens=20}"
    )
    pub.add(derive_facts(s, voice()))
    bucket = next(iter(pub.buckets().values()))
    assert bucket.barge_in_spans == 1
    assert bucket.barge_in_discarded_tokens == 500


def test_output_ceiling_hit_detected():
    pub = MetricPublisher()
    pub.add(
        derive_facts(
            span(
                usage_output_tokens=1900,
                request_max_tokens=2048,
                usage_input_tokens=100,
                usage_total_tokens=2000,
            ),
            voice(),
        )
    )
    bucket = next(iter(pub.buckets().values()))
    assert bucket.output_ceiling_hits == 1


def test_span_with_absent_total_excluded_from_totals():
    pub = MetricPublisher()
    pub.add(derive_facts(span(usage_input_tokens=100, usage_output_tokens=10), voice()))
    bucket = next(iter(pub.buckets().values()))
    assert bucket.total_tokens == 0  # nothing to count
    assert bucket.input_tokens == 100  # but the parts are still visible


def test_cache_hit_ratio_none_when_caching_inactive():
    """None, not zero - the distinction the whole cache metric rests on."""
    pub = MetricPublisher()
    pub.add(
        derive_facts(
            span(usage_input_tokens=100, usage_total_tokens=100), voice()
        )
    )
    bucket = next(iter(pub.buckets().values()))
    assert bucket.cache_hit_ratio is None


@pytest.mark.golden
def test_publisher_totals_match_manifest(inference_spans, manifest):
    pub = MetricPublisher()
    for s in inference_spans:
        cid = s.text("initial_contact_id") or "unknown"
        pub.add(derive_facts(s, ChannelInfo(contact_id=cid, channel="VOICE")))

    channel_bucket = pub.buckets()[(("channel", "VOICE"),)]
    assert channel_bucket.total_tokens == manifest["tokens"]["total"]
    assert channel_bucket.input_tokens == manifest["tokens"]["input_fresh"]
    assert channel_bucket.output_tokens == manifest["tokens"]["output"]
    assert channel_bucket.cache_read_tokens == manifest["tokens"]["cache_read"]
    assert channel_bucket.inference_count == len(inference_spans)
    assert round(channel_bucket.reasoning_share_incl_tool, 4) == manifest["reasoning"][
        "share_incl_tool"
    ]
