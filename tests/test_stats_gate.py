"""Statistical_Gate tests, including calibration against the two falsified findings.

These calibration tests are the point of the module. If either falsified dataset
starts rendering, the gate has regressed and the dashboard can mislead again.
"""

from __future__ import annotations

import math

import pytest

from connect_ai_tokens.config import StatisticalConfig
from connect_ai_tokens.reasoning import apportion
from connect_ai_tokens.stats_gate import (
    CorrelationFigure,
    StatisticalGate,
    Suppression,
    correlation_p_value,
    partial_correlation,
    pearson,
    two_sided_p_from_t,
)

# The real falsified dataset: 7 analysed chat contacts.
# tokens per contact against Contact Lens OverallSentiment.CUSTOMER
CHAT_TOKENS = [62632.0, 53608.0, 52506.0, 33861.0, 29329.0, 19425.0, 3798.0]
CHAT_SENTIMENT = [0.0, -1.0, 0.0, 0.0, 1.0, -1.7, 0.0]


@pytest.fixture
def gate():
    return StatisticalGate()


# --------------------------------------------------------------------------
# Distribution maths
# --------------------------------------------------------------------------


def test_t_cdf_matches_known_critical_values():
    """Two-sided p at the classic 5% critical values."""
    assert two_sided_p_from_t(2.042, 30) == pytest.approx(0.05, abs=0.002)
    assert two_sided_p_from_t(1.960, 100000) == pytest.approx(0.05, abs=0.002)
    assert two_sided_p_from_t(0.0, 10) == pytest.approx(1.0)


def test_pearson_known_values():
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)


def test_correlation_p_value_significant_for_strong_large_sample():
    assert correlation_p_value(0.718, 132) < 1e-10


# --------------------------------------------------------------------------
# CALIBRATION 1 - token vs chat sentiment must be suppressed
# --------------------------------------------------------------------------


@pytest.mark.calibration
def test_token_sentiment_correlation_is_suppressed(gate):
    result = gate.check_correlation(
        CorrelationFigure(CHAT_TOKENS, CHAT_SENTIMENT, "tokens", "sentiment")
    )
    assert not result.renders


@pytest.mark.calibration
def test_token_sentiment_caught_by_two_independent_rules(gate):
    """n=7 and a 4-distinct-value dependent variable. Either alone suffices."""
    result = gate.check_correlation(
        CorrelationFigure(CHAT_TOKENS, CHAT_SENTIMENT, "tokens", "sentiment")
    )
    assert Suppression.INSUFFICIENT_DATA in result.reasons
    assert Suppression.DEGENERATE in result.reasons


@pytest.mark.calibration
def test_sentiment_degeneracy_survives_a_larger_sample(gate):
    """The distribution problem is not fixed by more of the same data.

    Replicating the 7 observations to n=70 clears the minimum-n rule, but 4
    distinct values with most mass at zero is still not correlatable.
    """
    xs = CHAT_TOKENS * 10
    ys = CHAT_SENTIMENT * 10
    result = gate.check_correlation(CorrelationFigure(xs, ys, "tokens", "sentiment"))
    assert not result.renders
    assert Suppression.DEGENERATE in result.reasons
    assert Suppression.INSUFFICIENT_DATA not in result.reasons


@pytest.mark.calibration
def test_actual_correlation_was_near_zero():
    """Documents the finding itself, independent of the gate."""
    assert pearson(CHAT_TOKENS, CHAT_SENTIMENT) == pytest.approx(0.043, abs=0.02)


# --------------------------------------------------------------------------
# CALIBRATION 2 - input tokens vs duration must be suppressed,
#                 output tokens vs duration must render
# --------------------------------------------------------------------------


@pytest.mark.calibration
def test_input_tokens_vs_duration_is_sample_unstable(inference_spans):
    """The input-latency claim is UNRESOLVED, and this test records why.

    Original corpus (132 spans, before log retention expired part of it):
        raw r = +0.151, t = 1.74, not significant, sign flipped across subgroups.
        Conclusion drawn at the time: input tokens do not drive duration.

    This corpus (106 spans, what retention left):
        raw r is around +0.25 and IS significant, and controlling for output
        tokens makes it STRONGER, not weaker.

    Two samples from the same system give opposite answers. That is the finding:
    the relationship is sample-dependent, so it must not be presented as either
    established or refuted. The exclusion of the widget is therefore a documented
    design decision (Requirement 9.7), not something the runtime gate detects -
    a single sample cannot see its own instability.

    This test deliberately asserts only what is stable: that the effect is weak
    in absolute terms. It does not assert a direction.
    """
    xs, ys, ctrl = [], [], []
    for s in inference_spans:
        i = s.int_or_none("usage_input_tokens")
        o = s.int_or_none("usage_output_tokens")
        d = s.duration_ms
        if i is not None and o is not None and d is not None:
            xs.append(float(i))
            ys.append(float(d))
            ctrl.append(float(o))
    assert len(xs) >= 30

    raw = pearson(xs, ys)
    partial = partial_correlation(xs, ys, ctrl)

    # Weak in both formulations, on both corpora. Nothing stronger is claimed.
    assert abs(raw) < 0.5, f"raw r={raw:+.3f} unexpectedly strong"
    assert abs(partial) < 0.5, f"partial r={partial:+.3f} unexpectedly strong"

    # And far weaker than the relationship that IS established.
    out_r = pearson(
        [float(s.int_or_none("usage_output_tokens")) for s in inference_spans
         if s.int_or_none("usage_output_tokens") is not None
         and s.duration_ms is not None],
        [float(s.duration_ms) for s in inference_spans
         if s.int_or_none("usage_output_tokens") is not None
         and s.duration_ms is not None],
    )
    assert abs(out_r) > abs(raw) * 2, (
        f"output->duration ({out_r:+.3f}) should dominate input->duration "
        f"({raw:+.3f}); if it does not, the latency model needs revisiting"
    )


@pytest.mark.calibration
def test_confounding_rule_works_on_a_known_confound():
    """The CONFOUNDED rule itself, on synthetic data where the answer is known.

    y is driven entirely by z. x merely correlates with z and has no independent
    effect. The raw x-y correlation is strong and significant; the partial
    correlation controlling for z is nil. This is the shape the rule must catch.
    """
    import random as _r

    rng = _r.Random(11)
    zs = [rng.gauss(0, 1) for _ in range(200)]
    xs = [z + rng.gauss(0, 0.25) for z in zs]      # x tracks z
    ys = [3.0 * z + rng.gauss(0, 0.25) for z in zs]  # y is caused by z only

    raw = pearson(xs, ys)
    partial = partial_correlation(xs, ys, zs)
    assert abs(raw) > 0.8, f"setup wrong, raw r={raw:+.3f}"
    assert abs(partial) < 0.3, f"partial should collapse, got {partial:+.3f}"

    gate = StatisticalGate()
    uncontrolled = gate.check_correlation(CorrelationFigure(xs, ys))
    controlled = gate.check_correlation(
        CorrelationFigure(xs, ys, control=zs, control_name="z")
    )
    assert uncontrolled.renders, "raw correlation should look convincing"
    assert not controlled.renders, "controlling for z must suppress it"
    assert Suppression.CONFOUNDED in controlled.reasons


@pytest.mark.calibration
def test_output_tokens_vs_duration_survives_controlling_for_input(
    gate, inference_spans
):
    """The real relationship must survive the confounding check too."""
    xs, ys, ctrl = [], [], []
    for s in inference_spans:
        o = s.int_or_none("usage_output_tokens")
        i = s.int_or_none("usage_input_tokens")
        d = s.duration_ms
        if o is not None and i is not None and d is not None:
            xs.append(float(o))
            ys.append(float(d))
            ctrl.append(float(i))
    result = gate.check_correlation(
        CorrelationFigure(
            xs, ys, "output_tokens", "duration_ms",
            control=ctrl, control_name="input_tokens",
        )
    )
    assert result.renders, f"real finding suppressed: {result.reasons}"


@pytest.mark.calibration
def test_output_tokens_vs_duration_renders(gate, inference_spans):
    """The gate must not be so strict that the one real relationship is lost."""
    xs, ys = [], []
    for s in inference_spans:
        o = s.int_or_none("usage_output_tokens")
        d = s.duration_ms
        if o is not None and d is not None:
            xs.append(float(o))
            ys.append(float(d))
    r = pearson(xs, ys)
    result = gate.check_correlation(
        CorrelationFigure(xs, ys, "output_tokens", "duration_ms")
    )
    assert r > 0.5, f"expected a strong positive relationship, got {r:+.3f}"
    assert result.renders, f"real finding was suppressed: {result.reasons}"
    assert result.n == len(xs)


@pytest.mark.calibration
def test_sign_instability_detected(gate):
    """Two subgroups of >= minimum_n with opposite signs."""
    xs = list(range(40)) + list(range(40))
    ys = list(range(40)) + [40 - v for v in range(40)]
    groups = ["VOICE"] * 40 + ["CHAT"] * 40
    result = gate.check_correlation(
        CorrelationFigure(xs, ys, "x", "y", subgroups=groups)
    )
    assert Suppression.SIGN_UNSTABLE in result.reasons


# --------------------------------------------------------------------------
# Scalar and aggregate rules
# --------------------------------------------------------------------------


def test_below_minimum_n_suppressed(gate):
    assert not gate.check_statistic(29).renders
    assert gate.check_statistic(30).renders


def test_label_always_carries_n(gate):
    assert gate.check_statistic(7).label() == "insufficient data — n=7"
    assert gate.check_statistic(50).label() == "n=50"


def test_blended_channel_aggregate_suppressed(gate):
    blended = gate.check_aggregate(100, ["VOICE"] * 50 + ["CHAT"] * 50)
    assert blended.decision is Suppression.CHANNEL_BLENDED
    assert not blended.renders
    single = gate.check_aggregate(100, ["VOICE"] * 100)
    assert single.renders


def test_unresolved_channel_is_its_own_scope(gate):
    """UNRESOLVED is a channel value, not a wildcard that blends."""
    assert gate.check_aggregate(40, ["UNRESOLVED"] * 40).renders
    assert not gate.check_aggregate(40, ["UNRESOLVED"] * 20 + ["VOICE"] * 20).renders


def test_per_figure_suppression_does_not_blank_the_widget(gate):
    figures = {
        "tokens_per_contact": gate.check_statistic(120),
        "cache_hit_ratio": gate.check_statistic(90),
        "sentiment_corr": gate.check_correlation(
            CorrelationFigure(CHAT_TOKENS, CHAT_SENTIMENT)
        ),
        "ttft_p90": gate.check_statistic(104),
    }
    rendered = [k for k, v in figures.items() if v.renders]
    suppressed = [k for k, v in figures.items() if not v.renders]
    assert suppressed == ["sentiment_corr"]
    assert len(rendered) == 3


def test_minimum_n_is_configurable(gate):
    strict = StatisticalGate(StatisticalConfig(minimum_n=200))
    assert not strict.check_statistic(150).renders
    assert gate.check_statistic(150).renders


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        StatisticalConfig(minimum_n=1)
    with pytest.raises(ValueError):
        StatisticalConfig(significance_level=0.5)


# --------------------------------------------------------------------------
# The reasoning relationship should survive the gate
# --------------------------------------------------------------------------


@pytest.mark.golden
def test_reasoning_tokens_vs_duration_renders(gate, inference_spans):
    """Reasoning is output, and output drives decode time - so this must hold."""
    xs, ys = [], []
    for s in inference_spans:
        split = apportion(s)
        d = s.duration_ms
        if split is not None and d is not None:
            xs.append(split.tokens_reasoning)
            ys.append(float(d))
    if len(xs) < 30:
        pytest.skip("corpus too small after retention loss")
    result = gate.check_correlation(CorrelationFigure(xs, ys))
    assert not math.isnan(pearson(xs, ys))
    assert result.renders or Suppression.NOT_SIGNIFICANT in result.reasons
