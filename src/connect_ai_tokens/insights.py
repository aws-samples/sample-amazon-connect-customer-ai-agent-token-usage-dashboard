"""Insights_Engine — lightweight implementations of the six intelligence components.

These operate over the curated views (via Athena or in-memory for tests) and
surface findings that a plain metric cannot express. Each component is gated by
Statistical_Gate, scoped to one channel, and derives thresholds from the
deploying account rather than shipping constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from . import constants as C
from .config import StatisticalConfig
from .stats_gate import CorrelationFigure, StatisticalGate, Suppression


@dataclass(frozen=True)
class Finding:
    component: str
    severity: float  # higher = worse, used for ranking
    agent: str
    metric: str
    message: str
    n: int
    suppressed: bool = False
    suppression_reason: str = ""


# --------------------------------------------------------------------------
# Regression_Detector
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VersionPair:
    agent: str
    old_version: str
    new_version: str
    channel: str


def detect_regression(
    pair: VersionPair,
    old_values: Sequence[float],
    new_values: Sequence[float],
    metric_name: str,
    gate: StatisticalGate,
    regression_pct: float = 0.10,
) -> Finding | None:
    """Compare one metric between two consecutive versions."""
    old_n = len(old_values)
    new_n = len(new_values)

    # Gate: both sides must pass minimum_n
    for n in (old_n, new_n):
        check = gate.check_statistic(n)
        if not check.renders:
            return Finding(
                "Regression_Detector", 0, pair.agent, metric_name,
                f"insufficient data — n={min(old_n, new_n)}", min(old_n, new_n),
                suppressed=True, suppression_reason=check.label(),
            )

    old_mean = sum(old_values) / old_n
    new_mean = sum(new_values) / new_n
    if old_mean == 0:
        return None
    change = (new_mean - old_mean) / old_mean
    if change <= regression_pct:
        return None  # no regression

    return Finding(
        "Regression_Detector",
        severity=abs(change),
        agent=pair.agent,
        metric=metric_name,
        message=(
            f"{metric_name} degraded {change:+.1%}: "
            f"v{pair.old_version} avg={old_mean:.1f} (n={old_n}) → "
            f"v{pair.new_version} avg={new_mean:.1f} (n={new_n})"
        ),
        n=old_n + new_n,
    )


# --------------------------------------------------------------------------
# Recommendation_Engine
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    action: str
    agent: str
    evidence: str
    estimated_seconds_saved: float | None
    confidence_interval: tuple[float, float] | None
    n: int
    caveat: str = ""


def recommend_enable_caching(
    agent: str,
    cache_state: str,
    model_supports_caching: bool,
    tokens_per_turn: float,
    turns_per_contact: float,
    n: int,
    gate: StatisticalGate,
) -> Recommendation | None:
    """Recommendation to enable prompt caching."""
    if cache_state != "OFF" or not model_supports_caching:
        return None
    check = gate.check_statistic(n)
    if not check.renders:
        return None

    # Conservative: estimate the system prompt portion that would be cached
    # (in the sample corpus, the first turn's cache_write was 4,301 tokens)
    est_saved_tokens = tokens_per_turn * 0.5  # system prompt is ~50% of turn 1
    est_seconds = est_saved_tokens * turns_per_contact * C.MS_PER_1K_INPUT_TOKENS_TTFT / 1000

    return Recommendation(
        action="Enable prompt caching",
        agent=agent,
        evidence=f"Cache state is OFF. Model supports caching. {tokens_per_turn:.0f} tokens/turn × {turns_per_contact:.1f} turns/contact",
        estimated_seconds_saved=est_seconds,
        confidence_interval=None,  # TTFT coefficient applies, not output coefficient
        n=n,
        caveat=(
            "No customer-facing caching toggle was found in GetAIAgent or "
            "GetAIPrompt. Model identity, prompt size, template cachePoint "
            "presence and rollout date were each tested and eliminated as the "
            "cause. Present this as evidence to raise with the service team."
        ),
    )


def recommend_reduce_reasoning(
    agent: str,
    reasoning_share: float,
    reasoning_tokens_per_contact: float,
    cohort_median_share: float,
    n: int,
    gate: StatisticalGate,
) -> Recommendation | None:
    """Recommendation to reduce reasoning verbosity."""
    if reasoning_share <= cohort_median_share:
        return None
    check = gate.check_statistic(n)
    if not check.renders:
        return None

    # Benefit: reduce to cohort median
    reduction = reasoning_tokens_per_contact * (1 - cohort_median_share / reasoning_share)
    est_seconds = reduction * C.MS_PER_OUTPUT_TOKEN / 1000
    ci_lo = reduction * C.MS_PER_OUTPUT_TOKEN_CI[0] / 1000
    ci_hi = reduction * C.MS_PER_OUTPUT_TOKEN_CI[1] / 1000

    return Recommendation(
        action="Reduce reasoning verbosity",
        agent=agent,
        evidence=f"Reasoning share {reasoning_share:.1%} exceeds cohort median {cohort_median_share:.1%}",
        estimated_seconds_saved=est_seconds,
        confidence_interval=(ci_lo, ci_hi),
        n=n,
    )


def recommend_trim_instructions(
    agent: str,
    instruction_tokens: float,
    cohort_median_tokens: float,
    calls_per_contact: float,
    n: int,
    gate: StatisticalGate,
) -> Recommendation | None:
    """Recommendation to trim system instructions."""
    if instruction_tokens <= cohort_median_tokens:
        return None
    check = gate.check_statistic(n)
    if not check.renders:
        return None

    reduction = instruction_tokens - cohort_median_tokens
    est_seconds = reduction * calls_per_contact * C.MS_PER_1K_INPUT_TOKENS_TTFT / 1000 / 1000

    return Recommendation(
        action="Trim system instructions",
        agent=agent,
        evidence=f"Instruction size ~{instruction_tokens:.0f} tokens exceeds cohort median ~{cohort_median_tokens:.0f}",
        estimated_seconds_saved=est_seconds,
        confidence_interval=None,
        n=n,
    )


def rank_recommendations(recs: list[Recommendation]) -> list[Recommendation]:
    """Ranked by estimated seconds saved, descending."""
    return sorted(recs, key=lambda r: -(r.estimated_seconds_saved or 0))


# --------------------------------------------------------------------------
# Comparison_Service
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CohortMetrics:
    """Aggregated metrics for one cohort (agent, version, or model)."""

    cohort_label: str
    channel: str
    n_spans: int
    n_contacts: int
    tokens_per_contact: float | None
    ms_per_output_token: float | None
    median_ttft_ms: float | None
    p90_ttft_ms: float | None
    cache_hit_ratio: float | None
    reasoning_share: float | None
    avg_output_tokens: float | None
    total_tokens: int


@dataclass(frozen=True)
class ComparisonResult:
    """Side-by-side cohort comparison, or a suppression reason."""

    cohorts: list[CohortMetrics]
    channel: str
    dimension: str  # "ai_agent_name", "ai_agent_version", "request_model"
    suppressed_cohorts: list[str]  # cohort labels below Minimum_N
    renders: bool


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = int(len(s) * pct)
    return s[min(idx, len(s) - 1)]


@dataclass
class CohortInput:
    """Raw span-level data for one cohort, ready for aggregation."""

    label: str
    channel: str
    contact_ids: list[str]
    usage_total_tokens: list[int]
    usage_output_tokens: list[int]
    duration_ms: list[int]
    ttft_ms: list[int]
    cache_read_tokens: list[int]
    input_tokens: list[int]
    chars_reasoning: list[int]
    chars_text: list[int]
    chars_tool: list[int]


def aggregate_cohort(inp: CohortInput) -> CohortMetrics:
    """Aggregate raw span data into cohort-level metrics.

    Computes ms_per_output_token WITHIN this cohort rather than applying the
    global 9.25 coefficient. This is the key fairness requirement: a model that
    decodes faster should show it.
    """
    n_spans = len(inp.usage_total_tokens)
    n_contacts = len(set(inp.contact_ids)) if inp.contact_ids else 0
    total_tokens = sum(inp.usage_total_tokens)

    # Tokens per contact
    tokens_per_contact = (
        total_tokens / n_contacts if n_contacts > 0 else None
    )

    # ms per output token — computed within this cohort, not the global coefficient
    total_duration = sum(inp.duration_ms) if inp.duration_ms else 0
    total_output = sum(inp.usage_output_tokens) if inp.usage_output_tokens else 0
    ms_per_output_token = (
        total_duration / total_output if total_output > 0 else None
    )

    # TTFT
    median_ttft = _median([float(t) for t in inp.ttft_ms])
    p90_ttft = _percentile([float(t) for t in inp.ttft_ms], 0.90)

    # Cache hit ratio
    total_cache_read = sum(inp.cache_read_tokens)
    total_input = sum(inp.input_tokens)
    cache_denom = total_cache_read + total_input
    cache_hit_ratio = total_cache_read / cache_denom if cache_denom > 0 else None

    # Reasoning share (incl tool)
    total_reasoning_chars = sum(inp.chars_reasoning)
    total_chars = total_reasoning_chars + sum(inp.chars_text) + sum(inp.chars_tool)
    reasoning_share = (
        total_reasoning_chars / total_chars if total_chars > 0 else None
    )

    avg_output = total_output / n_spans if n_spans > 0 else None

    return CohortMetrics(
        cohort_label=inp.label,
        channel=inp.channel,
        n_spans=n_spans,
        n_contacts=n_contacts,
        tokens_per_contact=tokens_per_contact,
        ms_per_output_token=ms_per_output_token,
        median_ttft_ms=median_ttft,
        p90_ttft_ms=p90_ttft,
        cache_hit_ratio=cache_hit_ratio,
        reasoning_share=reasoning_share,
        avg_output_tokens=avg_output,
        total_tokens=total_tokens,
    )


def compare_cohorts(
    cohort_inputs: list[CohortInput],
    dimension: str,
    gate: StatisticalGate,
) -> ComparisonResult:
    """Compare two or more cohorts with Minimum_N gating.

    Requirements satisfied:
      19.1 — Supports comparison across agent, version, prompt, model cohorts
      19.2 — Withholds below Minimum_N with "insufficient data — n=<count>"
      19.3 — Normalises to per-contact basis (tokens_per_contact)
      19.4 — Computes ms_per_output_token within each cohort
      19.5 — Scoped to single channel (enforced by caller, validated here)
      19.6 — Displays observation count for every cohort
    """
    if not cohort_inputs:
        return ComparisonResult(
            cohorts=[], channel="", dimension=dimension,
            suppressed_cohorts=[], renders=False,
        )

    # Validate single channel
    channels = set(c.channel for c in cohort_inputs)
    if len(channels) > 1:
        raise ValueError(
            f"Comparison must be scoped to a single channel, got: {channels}"
        )
    channel = cohort_inputs[0].channel

    cohorts: list[CohortMetrics] = []
    suppressed: list[str] = []

    for inp in cohort_inputs:
        metrics = aggregate_cohort(inp)
        check = gate.check_statistic(metrics.n_spans)
        if not check.renders:
            suppressed.append(f"{inp.label} (n={metrics.n_spans})")
        cohorts.append(metrics)

    # Renders only if at least 2 cohorts pass the gate
    passing = [c for c in cohorts if c.cohort_label not in
               [s.split(" (")[0] for s in suppressed]]
    renders = len(passing) >= 2

    return ComparisonResult(
        cohorts=cohorts,
        channel=channel,
        dimension=dimension,
        suppressed_cohorts=suppressed,
        renders=renders,
    )


def format_comparison(result: ComparisonResult) -> str:
    """Human-readable comparison table."""
    if not result.cohorts:
        return "No cohorts provided."

    lines = [
        f"Comparison by {result.dimension} | Channel: {result.channel}",
        "-" * 80,
    ]

    if result.suppressed_cohorts:
        lines.append(
            f"Suppressed (insufficient data): {', '.join(result.suppressed_cohorts)}"
        )
        lines.append("")

    # Header
    headers = [
        "Cohort", "n_spans", "contacts", "tok/contact",
        "ms/out_tok", "TTFT_p50", "TTFT_p90", "cache%", "reasoning%",
    ]
    lines.append("  ".join(f"{h:>12}" for h in headers))
    lines.append("-" * 80)

    for c in result.cohorts:
        suppressed = c.cohort_label in [s.split(" (")[0] for s in result.suppressed_cohorts]
        if suppressed:
            lines.append(
                f"{c.cohort_label:>12}  "
                f"insufficient data — n={c.n_spans}"
            )
        else:
            lines.append("  ".join([
                f"{c.cohort_label[:12]:>12}",
                f"{c.n_spans:>12}",
                f"{c.n_contacts:>12}",
                f"{c.tokens_per_contact:>12,.0f}" if c.tokens_per_contact else f"{'—':>12}",
                f"{c.ms_per_output_token:>12.2f}" if c.ms_per_output_token else f"{'—':>12}",
                f"{c.median_ttft_ms:>12,.0f}" if c.median_ttft_ms else f"{'—':>12}",
                f"{c.p90_ttft_ms:>12,.0f}" if c.p90_ttft_ms else f"{'—':>12}",
                f"{c.cache_hit_ratio:>12.1%}" if c.cache_hit_ratio else f"{'—':>12}",
                f"{c.reasoning_share:>12.1%}" if c.reasoning_share else f"{'—':>12}",
            ]))

    if not result.renders:
        lines.append("")
        lines.append("Comparison not rendered: fewer than 2 cohorts pass Minimum_N gate.")

    return "\n".join(lines)
