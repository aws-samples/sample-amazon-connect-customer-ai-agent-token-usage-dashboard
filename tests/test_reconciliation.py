"""Token reconciliation tests. Property 1."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from connect_ai_tokens.reconciliation import (
    ReconciliationState,
    ReconciliationTally,
    reconcile,
)
from connect_ai_tokens.span_parser import parse_span


def span(**kw):
    body = ", ".join(f"{k}={v}" for k, v in kw.items())
    return parse_span("{span_name=inference, " + body + "}")


def test_identity_holds_without_cache():
    r = reconcile(
        span(usage_input_tokens=100, usage_output_tokens=20, usage_total_tokens=120)
    )
    assert r.state is ReconciliationState.OK
    assert r.delta == 0


def test_identity_holds_with_cache():
    """The real SelfService-Agent-B shape: fresh input is small, cache carries the rest."""
    r = reconcile(
        span(
            usage_input_tokens=488,
            usage_output_tokens=244,
            cache_read_input_tokens=0,
            cache_write_input_tokens=4301,
            usage_total_tokens=5033,
        )
    )
    assert r.state is ReconciliationState.OK


def test_input_plus_output_alone_undercounts_when_caching():
    """Why the identity exists: naive summing loses the cache term entirely."""
    s = span(
        usage_input_tokens=488,
        usage_output_tokens=244,
        cache_read_input_tokens=0,
        cache_write_input_tokens=4301,
        usage_total_tokens=5033,
    )
    naive = s.int_or_none("usage_input_tokens") + s.int_or_none("usage_output_tokens")
    assert s.int_or_none("usage_total_tokens") - naive == 4301


def test_mismatch_detected_and_retained():
    r = reconcile(
        span(usage_input_tokens=100, usage_output_tokens=20, usage_total_tokens=999)
    )
    assert r.state is ReconciliationState.MISMATCH
    assert r.reported_total == 999
    assert r.computed_sum == 120
    assert r.delta == 879
    assert r.state.counts_toward_totals  # retained, not dropped


def test_non_numeric_token_field_skips_verification():
    r = reconcile(span(usage_input_tokens="n/a", usage_total_tokens=120))
    assert r.state is ReconciliationState.SKIPPED_NON_NUMERIC
    assert "usage_input_tokens" in r.non_numeric_fields
    assert r.state.counts_toward_totals


def test_absent_total_excluded_from_totals():
    r = reconcile(span(usage_input_tokens=100, usage_output_tokens=20))
    assert r.state is ReconciliationState.SKIPPED_ABSENT_TOTAL
    assert not r.state.counts_toward_totals


def test_absent_cache_contributes_zero_to_verification_only():
    """Absence is zero for the identity check, and only for that."""
    s = span(usage_input_tokens=100, usage_output_tokens=20, usage_total_tokens=120)
    assert reconcile(s).state is ReconciliationState.OK
    assert not s.has_any_cache_field  # still absent for Cache_State purposes


@pytest.mark.property
@settings(max_examples=300, deadline=None)
@given(
    st.integers(min_value=0, max_value=10**5),
    st.integers(min_value=0, max_value=10**4),
    st.integers(min_value=0, max_value=10**5),
    st.integers(min_value=0, max_value=10**5),
)
def test_identity_property(inp, out, cr, cw):
    """Property 1 over generated token combinations, all cache permutations."""
    total = inp + out + cr + cw
    r = reconcile(
        span(
            usage_input_tokens=inp,
            usage_output_tokens=out,
            cache_read_input_tokens=cr,
            cache_write_input_tokens=cw,
            usage_total_tokens=total,
        )
    )
    assert r.state is ReconciliationState.OK

    if total > 0:
        bad = reconcile(
            span(
                usage_input_tokens=inp,
                usage_output_tokens=out,
                cache_read_input_tokens=cr,
                cache_write_input_tokens=cw,
                usage_total_tokens=total + 1,
            )
        )
        assert bad.state is ReconciliationState.MISMATCH


@pytest.mark.golden
def test_captured_corpus_reconciles(inference_spans, manifest):
    """Zero mismatches across every real token-bearing span."""
    tally = ReconciliationTally()
    for s in inference_spans:
        tally.add(reconcile(s))

    expected = manifest["reconciliation"]
    assert tally.ok == expected["ok"]
    assert tally.mismatch == expected["mismatch"] == 0
    assert tally.skipped_non_numeric == expected["skipped_non_numeric"]
    assert tally.skipped_absent_total == expected["skipped_absent_total"]
    assert tally.total_considered == len(inference_spans)


@pytest.mark.golden
def test_corpus_token_identity_sums(inference_spans, manifest):
    """The aggregate identity, not just per span."""
    t = manifest["tokens"]
    total = sum(s.int_or_none("usage_total_tokens") or 0 for s in inference_spans)
    inp = sum(s.int_or_none("usage_input_tokens") or 0 for s in inference_spans)
    out = sum(s.int_or_none("usage_output_tokens") or 0 for s in inference_spans)
    cr = sum(s.int_or_none("cache_read_input_tokens") or 0 for s in inference_spans)
    cw = sum(s.int_or_none("cache_write_input_tokens") or 0 for s in inference_spans)

    assert total == inp + out + cr + cw
    assert t["identity_holds"] is True
    assert (total, inp, out, cr, cw) == (
        t["total"],
        t["input_fresh"],
        t["output"],
        t["cache_read"],
        t["cache_write"],
    )


@pytest.mark.golden
def test_input_dominates_consumption(manifest):
    """Input is ~94% of tokens - which is why caching is the lever, not brevity."""
    t = manifest["tokens"]
    assert t["input_share"] > 0.85
    assert t["output_share"] < 0.10
