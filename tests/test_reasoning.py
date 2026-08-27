"""Reasoning apportionment tests. Property 4."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from connect_ai_tokens.constants import MS_PER_OUTPUT_TOKEN
from connect_ai_tokens.reasoning import ReasoningTally, apportion
from connect_ai_tokens.span_parser import parse_span


def make_span(messages, output_tokens=100):
    payload = json.dumps(messages, separators=(",", ":"))
    return parse_span(
        "{span_name=inference, usage_output_tokens="
        f"{output_tokens}, output_messages={payload}}}"
    )


def msg(*values):
    return {"messageId": "m", "participant": "BOT", "values": list(values)}


def text(v):
    return {"text": {"value": v}}


def reasoning(v):
    return {"reasoning": {"value": v}}


def tool(name="Retrieve"):
    return {"toolUse": {"name": name, "input": "{}"}}


def test_splits_text_and_reasoning():
    s = make_span([msg(text("a" * 60), reasoning("b" * 40))], output_tokens=100)
    split = apportion(s)
    assert split.chars_text == 60
    assert split.chars_reasoning == 40
    assert split.chars_tool == 0
    assert split.tokens_text == pytest.approx(60.0)
    assert split.tokens_reasoning == pytest.approx(40.0)


def test_two_denominators_differ():
    """43.7% vs 51.2% on the real corpus - the distinction must be explicit."""
    s = make_span([msg(text("a" * 40), reasoning("b" * 40), tool())])
    split = apportion(s)
    assert split.share_excl_tool == pytest.approx(0.5)
    assert split.share_incl_tool < split.share_excl_tool


def test_zero_reasoning_is_a_real_zero_not_unapportioned():
    """39 of 132 real spans have no reasoning. That is data, not a failure."""
    split = apportion(make_span([msg(text("hello"))]))
    assert split is not None
    assert split.chars_reasoning == 0
    assert split.tokens_reasoning == 0.0
    assert split.share_incl_tool == 0.0


def test_unapportioned_when_output_messages_absent():
    s = parse_span("{span_name=inference, usage_output_tokens=100}")
    assert apportion(s) is None


def test_unapportioned_when_output_tokens_absent():
    payload = json.dumps([msg(text("hi"))], separators=(",", ":"))
    s = parse_span(f"{{span_name=inference, output_messages={payload}}}")
    assert apportion(s) is None


def test_unapportioned_when_output_messages_unparseable():
    s = parse_span("{span_name=inference, usage_output_tokens=100, output_messages=not-json}")
    assert apportion(s) is None


def test_reasoning_ms_uses_the_measured_coefficient():
    s = make_span([msg(reasoning("r" * 100))], output_tokens=200)
    split = apportion(s)
    assert split.tokens_reasoning == pytest.approx(200.0)
    assert split.reasoning_ms(MS_PER_OUTPUT_TOKEN) == pytest.approx(200 * 9.25)


@pytest.mark.property
@settings(max_examples=300, deadline=None)
@given(
    st.integers(min_value=0, max_value=500),
    st.integers(min_value=0, max_value=500),
    st.integers(min_value=1, max_value=5000),
)
def test_apportionment_conserves_tokens(n_text, n_reasoning, out_tokens):
    """Property 4: the three parts sum back to usage_output_tokens."""
    values = []
    if n_text:
        values.append(text("a" * n_text))
    if n_reasoning:
        values.append(reasoning("b" * n_reasoning))
    if not values:
        return
    split = apportion(make_span([msg(*values)], output_tokens=out_tokens))
    assert split is not None
    assert (
        split.tokens_text + split.tokens_reasoning + split.tokens_tool
    ) == pytest.approx(float(out_tokens))


@pytest.mark.property
@settings(max_examples=100, deadline=None)
@given(st.lists(st.integers(min_value=1, max_value=200), min_size=1, max_size=20))
def test_tally_conserves_across_many_spans(sizes):
    """Fractions summed before rounding, so aggregates do not drift."""
    tally = ReasoningTally()
    expected = 0
    for i, size in enumerate(sizes):
        out = size * 2
        expected += out
        tally.add(
            apportion(
                make_span([msg(text("a" * size), reasoning("b" * size))], out)
            )
        )
    assert tally.output_tokens == expected
    assert (
        tally.tokens_text + tally.tokens_reasoning + tally.tokens_tool
    ) == pytest.approx(float(expected))


@pytest.mark.golden
def test_captured_corpus_reasoning_split(inference_spans, manifest):
    tally = ReasoningTally()
    for s in inference_spans:
        tally.add(apportion(s))

    exp = manifest["reasoning"]
    assert tally.chars_text == exp["chars_text"]
    assert tally.chars_reasoning == exp["chars_reasoning"]
    assert tally.chars_tool == exp["chars_tool"]
    assert round(tally.share_incl_tool, 4) == exp["share_incl_tool"]
    assert round(tally.share_excl_tool, 4) == exp["share_excl_tool"]
    assert tally.apportioned_spans == exp["apportioned_spans"]
    assert tally.zero_reasoning_spans == exp["zero_reasoning_spans"]


@pytest.mark.golden
def test_reasoning_is_a_large_share_of_output(manifest):
    """The headline finding: roughly half of generated output is never seen."""
    exp = manifest["reasoning"]
    assert exp["share_excl_tool"] > 0.35
    assert exp["share_incl_tool"] > 0.30


@pytest.mark.golden
def test_reasoning_distribution_is_not_uniform(manifest):
    """A headline share alone would mislead - the spread must travel with it."""
    dist = manifest["reasoning"]["distribution"]
    assert dist["min"] == 0.0
    assert dist["max"] > dist["median"]
    assert dist["zero_reasoning_spans"] > 0


@pytest.mark.golden
def test_corpus_conserves_output_tokens(inference_spans):
    """Property 4 against production spans."""
    tally = ReasoningTally()
    for s in inference_spans:
        tally.add(apportion(s))
    assert (
        tally.tokens_text + tally.tokens_reasoning + tally.tokens_tool
    ) == pytest.approx(float(tally.output_tokens))
