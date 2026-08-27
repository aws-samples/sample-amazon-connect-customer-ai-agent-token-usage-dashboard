"""Span_Parser tests.

The parser is where correctness is won or lost, so this is the heaviest suite:
property-based tests for the invariants, then golden tests against real captured
spans for the shapes that actually occur in production.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from connect_ai_tokens import constants as C
from connect_ai_tokens.span_parser import (
    ABSENT,
    KNOWN_SPAN_FIELDS,
    SpanParseError,
    parse_span,
    serialize_span,
)

# --------------------------------------------------------------------------
# Targeted behaviour
# --------------------------------------------------------------------------


def test_parses_simple_map():
    s = parse_span("{span_id=abc, span_name=inference, usage_input_tokens=5823}")
    assert s.text("span_id") == "abc"
    assert s.span_name == "inference"
    assert s.int_or_none("usage_input_tokens") == 5823


def test_free_text_with_commas_survives():
    """The real shape that breaks a naive split on ', '."""
    s = parse_span(
        "{span_name=inference, status=ERROR, error_type=barge_in, "
        "status_description=Abandoning due to barge-in, usage_output_tokens=96}"
    )
    assert s.text("status_description") == "Abandoning due to barge-in"
    assert s.text("error_type") == "barge_in"
    assert s.int_or_none("usage_output_tokens") == 96


def test_nested_json_array_not_split():
    s = parse_span(
        '{span_name=inference, response_finish_reasons=["end_turn"], '
        'usage_total_tokens=5919}'
    )
    assert s.text("response_finish_reasons") == '["end_turn"]'
    assert s.int_or_none("usage_total_tokens") == 5919


def test_json_with_internal_commas_and_quotes():
    payload = '[{"a":1,"b":"x, y","c":[1,2,3]}]'
    s = parse_span(f"{{span_name=inference, input_messages={payload}, status=OK}}")
    assert s.text("input_messages") == payload
    assert s.text("status") == "OK"
    assert json.loads(s.text("input_messages"))[0]["c"] == [1, 2, 3]


def test_value_containing_equals_sign():
    s = parse_span("{span_name=inference, system_instructions=a=b=c}")
    assert s.text("system_instructions") == "a=b=c"


def test_absence_is_not_zero():
    """Property 2. The whole cache-state metric depends on this."""
    s = parse_span("{span_name=inference, usage_input_tokens=100}")
    assert s.is_absent("cache_read_input_tokens")
    assert s.get("cache_read_input_tokens") is ABSENT
    assert s.get("cache_read_input_tokens") != 0
    assert not s.has_any_cache_field


def test_cache_field_present_with_zero_counts_as_present():
    s = parse_span("{span_name=inference, cache_read_input_tokens=0}")
    assert s.has("cache_read_input_tokens")
    assert s.int_or_none("cache_read_input_tokens") == 0
    assert s.has_any_cache_field


def test_non_numeric_token_field_types_as_text():
    s = parse_span("{span_name=inference, usage_input_tokens=n/a}")
    assert s.text("usage_input_tokens") == "n/a"
    assert s.int_or_none("usage_input_tokens") is None


def test_unknown_field_retained_not_a_failure():
    s = parse_span("{span_name=inference, brand_new_field=42}")
    assert s.get("brand_new_field") == "42"
    assert "brand_new_field" not in KNOWN_SPAN_FIELDS


def test_duration_derived():
    s = parse_span("{span_name=inference, start_timestamp=1000, end_timestamp=3298}")
    assert s.duration_ms == 2298


def test_barge_in_discard_detected():
    s = parse_span(
        "{span_name=inference, status=ERROR, error_type=barge_in, "
        "usage_total_tokens=500}"
    )
    assert s.is_barge_in_discard


def test_tool_error_is_not_a_barge_in_discard():
    """A failed tool is a failure; a barge-in is paid-for work thrown away."""
    s = parse_span(
        "{span_name=inference, status=ERROR, error_type=tool_execution, "
        "usage_total_tokens=500}"
    )
    assert not s.is_barge_in_discard


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "   ",
        "no braces here",
        "{unbalanced",
        "{}",
        "{   }",
        "{no_equals_sign}",
        123,
    ],
)
def test_malformed_input_raises(bad):
    with pytest.raises(SpanParseError):
        parse_span(bad)


def test_oversized_span_rejected():
    with pytest.raises(SpanParseError):
        parse_span("{a=" + "x" * (C.MAX_SPAN_CHARS + 10) + "}")


def test_excessive_bracket_depth_rejected():
    deep = "[" * (C.MAX_BRACKET_DEPTH + 2) + "]" * (C.MAX_BRACKET_DEPTH + 2)
    with pytest.raises(SpanParseError):
        parse_span(f"{{span_name=inference, input_messages={deep}}}")


# --------------------------------------------------------------------------
# Property-based
# --------------------------------------------------------------------------

_safe_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs", "Cc"), blacklist_characters="{}[]()\\\"="
    ),
    min_size=0,
    max_size=40,
)
_keys = st.one_of(
    st.sampled_from(sorted(KNOWN_SPAN_FIELDS)),
    st.from_regex(r"\A[a-z][a-z0-9_]{2,15}\Z", fullmatch=True),
)


@st.composite
def span_strings(draw):
    n = draw(st.integers(min_value=1, max_value=12))
    keys = draw(
        st.lists(_keys, min_size=n, max_size=n, unique=True)
    )
    parts = []
    for k in keys:
        kind = draw(st.integers(min_value=0, max_value=3))
        if kind == 0:
            v = str(draw(st.integers(min_value=0, max_value=10**6)))
        elif kind == 1:
            v = draw(_safe_text)
        elif kind == 2:
            v = json.dumps(draw(st.lists(st.integers(), max_size=5)))
        else:
            v = json.dumps({"a": draw(st.integers()), "b": "x, y"})
        parts.append(f"{k}={v}")
    return "{" + ", ".join(parts) + "}"


@pytest.mark.property
@settings(max_examples=400, deadline=None)
@given(span_strings())
def test_round_trip_stability(text):
    """Property 3: parse(serialize(parse(x))) == parse(x).

    Stated over inputs the parser accepts. Byte-identity with the original is
    explicitly not required - only that a second pass is stable, which is what
    makes the KEY_HEAD split heuristic safe to rely on downstream.
    """
    try:
        first = parse_span(text)
    except SpanParseError:
        assume(False)
        return
    second = parse_span(serialize_span(first))
    assert dict(first.fields) == dict(second.fields)
    assert dict(first.unknown_fields) == dict(second.unknown_fields)
    assert set(first.keys()) == set(second.keys())
    for k in first.keys():
        assert type(first.get(k)) is type(second.get(k))


@pytest.mark.property
@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=0, max_value=10**7))
def test_token_fields_type_as_int_when_all_digits(value):
    s = parse_span(f"{{usage_total_tokens={value}}}")
    assert s.int_or_none("usage_total_tokens") == value


@pytest.mark.property
@settings(max_examples=200, deadline=None)
@given(_safe_text.filter(lambda t: not t.isdigit() and t != ""))
def test_absent_never_equals_zero(text):
    s = parse_span(f"{{span_name=inference, status={text or 'OK'}}}")
    for f in C.TOKEN_FIELDS:
        assert s.get(f) is ABSENT
        assert s.get(f) != 0


# --------------------------------------------------------------------------
# Golden - real captured spans
# --------------------------------------------------------------------------


@pytest.mark.golden
def test_every_captured_span_parses(trace_events, manifest):
    failures = []
    for e in trace_events:
        try:
            parse_span(e["span"])
        except SpanParseError as exc:
            failures.append(str(exc))
    assert failures == []
    assert len(trace_events) == manifest["corpus"]["trace_events"]
    assert manifest["corpus"]["parse_failures"] == 0


@pytest.mark.golden
def test_span_name_inventory(spans, manifest):
    from collections import Counter

    observed = {k: v for k, v in Counter(s.span_name for s in spans).items() if k}
    assert observed == manifest["corpus"]["span_names"]


@pytest.mark.golden
def test_round_trip_on_every_captured_span(spans):
    """Property 3 against production shapes, not just generated ones."""
    for s in spans:
        again = parse_span(serialize_span(s))
        assert dict(again.fields) == dict(s.fields)
        assert dict(again.unknown_fields) == dict(s.unknown_fields)


@pytest.mark.golden
def test_unknown_field_canary(spans):
    """Canary for service-side schema drift.

    Unknown fields are tolerated by design, so this is not a correctness gate —
    it is a signal. When it fails, a new span field has appeared: review it, add
    it to KNOWN_SPAN_FIELDS, and decide whether it carries a metric worth
    surfacing. `trace_id` was found exactly this way.
    """
    unexpected = {k for s in spans for k in s.unknown_fields}
    assert unexpected == set(), (
        f"new span field(s) from the service: {sorted(unexpected)} — "
        "tolerated at runtime, but review and add to KNOWN_SPAN_FIELDS"
    )


@pytest.mark.golden
def test_timestamps_and_durations_typed(inference_spans):
    """Regression: *_timestamp must type as int or duration_ms silently breaks."""
    typed = [s for s in inference_spans if s.duration_ms is not None]
    assert len(typed) == len(inference_spans)
    assert all(s.duration_ms >= 0 for s in typed)


@pytest.mark.golden
def test_cache_fields_absent_on_non_caching_agents(inference_spans, manifest):
    off_agents = {
        a for a, v in manifest["cache"]["state_by_agent"].items() if v["state"] == "OFF"
    }
    for s in inference_spans:
        agent = s.text("ai_agent_name") or C.DIMENSION_UNKNOWN
        if agent in off_agents:
            assert not s.has_any_cache_field
            for f in C.CACHE_FIELDS:
                assert s.get(f) is ABSENT
