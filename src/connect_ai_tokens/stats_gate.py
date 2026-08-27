"""Statistical_Gate — refuses to render a figure that would mislead.

This module exists because two plausible-looking findings were produced during
validation and both were wrong:

1. "Token burn predicts customer dissatisfaction" — r = +0.043, n = 7, and the
   sentiment variable took only 4 distinct values with 4 of 7 observations at
   exactly zero. A correlation against a near-constant.
2. "Input tokens drive invocation latency" — t = 1.74, the bootstrap CI crossed
   zero, and the sign flipped across subgroups.

The gate is calibrated so that each is caught by at least two independent rules,
while the one relationship that *is* real (output tokens drive decode time,
r = +0.718, n = 132) still renders. A gate that suppresses that too would be
useless.

No third-party statistics dependency: the Student's t CDF is computed from the
regularised incomplete beta function so the Lambda stays dependency-light.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from .config import StatisticalConfig


class Suppression(str, Enum):
    RENDER = "RENDER"
    INSUFFICIENT_DATA = "insufficient_data"
    DEGENERATE = "degenerate"
    NOT_SIGNIFICANT = "not_significant"
    SIGN_UNSTABLE = "sign_unstable"
    CONFOUNDED = "confounded"
    CHANNEL_BLENDED = "channel_blended"


@dataclass(frozen=True)
class GateResult:
    decision: Suppression
    n: int
    detail: str = ""
    reasons: tuple[Suppression, ...] = ()

    @property
    def renders(self) -> bool:
        return self.decision is Suppression.RENDER

    def label(self) -> str:
        """The exact string a widget shows in place of a suppressed figure."""
        match self.decision:
            case Suppression.RENDER:
                return f"n={self.n}"
            case Suppression.INSUFFICIENT_DATA:
                return f"insufficient data — n={self.n}"
            case Suppression.DEGENERATE:
                return f"degenerate distribution — {self.detail}, n={self.n}"
            case Suppression.NOT_SIGNIFICANT:
                return f"not significant at n={self.n}"
            case Suppression.SIGN_UNSTABLE:
                return f"sign unstable across subgroups — n={self.n}"
            case Suppression.CONFOUNDED:
                return f"confounded by {self.detail} — n={self.n}"
            case Suppression.CHANNEL_BLENDED:
                return "channel blended — scope to a single channel"
        return ""


# --------------------------------------------------------------------------
# Distribution maths (no scipy)
# --------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def regularised_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1.0 - x) / b


def two_sided_p_from_t(t: float, df: int) -> float:
    """Exact two-sided p-value for a t statistic."""
    if df <= 0:
        return 1.0
    return regularised_incomplete_beta(df / 2.0, 0.5, df / (df + t * t))


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    den = math.sqrt(dx * dy)
    return num / den if den else float("nan")


def correlation_p_value(r: float, n: int, df_offset: int = 2) -> float:
    if n <= df_offset or math.isnan(r) or abs(r) >= 1.0:
        return 0.0 if abs(r) >= 1.0 and n > df_offset else 1.0
    t = r * math.sqrt((n - df_offset) / (1.0 - r * r))
    return two_sided_p_from_t(t, n - df_offset)


def partial_correlation(
    xs: Sequence[float], ys: Sequence[float], zs: Sequence[float]
) -> float:
    """Correlation of x and y with the influence of z removed.

    This is the test that actually falsified "input tokens drive latency". The
    raw correlation can be significant purely because both variables track a
    third one — here, output tokens, which genuinely do drive decode time.
    """
    r_xy = pearson(xs, ys)
    r_xz = pearson(xs, zs)
    r_yz = pearson(ys, zs)
    if any(math.isnan(v) for v in (r_xy, r_xz, r_yz)):
        return float("nan")
    den = math.sqrt(max(0.0, (1 - r_xz**2) * (1 - r_yz**2)))
    return (r_xy - r_xz * r_yz) / den if den else float("nan")


def bootstrap_ci(
    xs: Sequence[float],
    ys: Sequence[float],
    resamples: int,
    seed: int = 7,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap CI for Pearson r. Deterministic for a given seed."""
    n = len(xs)
    if n < 3:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    pairs = list(zip(xs, ys))
    stats: list[float] = []
    for _ in range(resamples):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        r = pearson([p[0] for p in sample], [p[1] for p in sample])
        if not math.isnan(r):
            stats.append(r)
    if not stats:
        return (float("nan"), float("nan"))
    stats.sort()
    lo = stats[max(0, int((alpha / 2) * len(stats)) - 1)]
    hi = stats[min(len(stats) - 1, int((1 - alpha / 2) * len(stats)))]
    return (lo, hi)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@dataclass
class CorrelationFigure:
    """A correlation a widget wants to display."""

    xs: Sequence[float]
    ys: Sequence[float]
    x_name: str = "x"
    y_name: str = "y"
    subgroups: Sequence[str] = field(default_factory=tuple)
    """Per-observation subgroup label (channel and/or model) for sign stability."""

    control: Sequence[float] | None = None
    """A known stronger driver to control for, if one is suspected.

    Supply this whenever the candidate driver could be tracking something else.
    For any latency claim, the control is output tokens: they demonstrably drive
    decode time, so an uncontrolled correlation against duration is untrustworthy.
    """

    control_name: str = "control"


class StatisticalGate:
    def __init__(self, config: StatisticalConfig | None = None) -> None:
        self.config = config or StatisticalConfig()

    # -- scalar statistics ---------------------------------------------------
    def check_statistic(self, n: int) -> GateResult:
        if n < self.config.minimum_n:
            return GateResult(Suppression.INSUFFICIENT_DATA, n)
        return GateResult(Suppression.RENDER, n)

    def check_aggregate(self, n: int, channels: Sequence[str]) -> GateResult:
        """An aggregate spanning contacts must be scoped to one channel."""
        distinct = {c for c in channels}
        if len(distinct) > 1:
            return GateResult(
                Suppression.CHANNEL_BLENDED, n, detail=",".join(sorted(distinct))
            )
        return self.check_statistic(n)

    # -- correlations --------------------------------------------------------
    def _degenerate(self, values: Sequence[float]) -> str | None:
        distinct = len(set(values))
        if distinct < self.config.minimum_distinct_values:
            return f"{distinct} distinct values"
        top = Counter(values).most_common(1)[0][1]
        if top / len(values) > self.config.maximum_single_value_share:
            return f"{top}/{len(values)} observations at one value"
        return None

    def _sign_unstable(self, fig: CorrelationFigure) -> bool:
        if not fig.subgroups or len(fig.subgroups) != len(fig.xs):
            return False
        buckets: dict[str, list[tuple[float, float]]] = {}
        for g, x, y in zip(fig.subgroups, fig.xs, fig.ys):
            buckets.setdefault(g, []).append((x, y))
        signs = set()
        for pairs in buckets.values():
            if len(pairs) < self.config.minimum_n:
                continue
            r = pearson([p[0] for p in pairs], [p[1] for p in pairs])
            if math.isnan(r) or r == 0:
                continue
            signs.add(r > 0)
        return len(signs) > 1

    def check_correlation(self, fig: CorrelationFigure) -> GateResult:
        """Every rule is evaluated so the caller can see all reasons, not just
        the first — a figure failing three ways is worth knowing about."""
        n = len(fig.xs)
        reasons: list[Suppression] = []
        detail = ""

        if n < self.config.minimum_n:
            reasons.append(Suppression.INSUFFICIENT_DATA)

        for values in (fig.xs, fig.ys):
            why = self._degenerate(values)
            if why:
                reasons.append(Suppression.DEGENERATE)
                detail = detail or why
                break

        r = pearson(fig.xs, fig.ys)
        if n >= 3 and not math.isnan(r):
            p = correlation_p_value(r, n)
            lo, hi = bootstrap_ci(fig.xs, fig.ys, self.config.bootstrap_resamples)
            crosses_zero = (
                not math.isnan(lo) and not math.isnan(hi) and lo <= 0.0 <= hi
            )
            if p > self.config.significance_level or crosses_zero:
                reasons.append(Suppression.NOT_SIGNIFICANT)
        else:
            reasons.append(Suppression.NOT_SIGNIFICANT)

        if self._sign_unstable(fig):
            reasons.append(Suppression.SIGN_UNSTABLE)

        # Confounding: a raw correlation that collapses once a known stronger
        # driver is controlled for is not a finding. This is the rule that
        # falsified "input tokens drive latency" - the raw r can be significant
        # while the partial r is not.
        if fig.control is not None and len(fig.control) == n and n > 3:
            pr = partial_correlation(fig.xs, fig.ys, fig.control)
            if math.isnan(pr):
                reasons.append(Suppression.CONFOUNDED)
                detail = detail or fig.control_name
            else:
                p_partial = correlation_p_value(pr, n, df_offset=3)
                if p_partial > self.config.significance_level:
                    reasons.append(Suppression.CONFOUNDED)
                    detail = detail or f"{fig.control_name} (partial r={pr:+.3f})"

        if reasons:
            # Report the most specific reason first; keep all for diagnostics.
            order = [
                Suppression.DEGENERATE,
                Suppression.INSUFFICIENT_DATA,
                Suppression.CONFOUNDED,
                Suppression.SIGN_UNSTABLE,
                Suppression.NOT_SIGNIFICANT,
            ]
            primary = next(x for x in order if x in reasons)
            return GateResult(primary, n, detail=detail, reasons=tuple(reasons))

        return GateResult(Suppression.RENDER, n, detail=f"r={r:+.3f}")


def suppress_per_figure(
    gate: StatisticalGate, figures: dict[str, GateResult]
) -> dict[str, str]:
    """Requirement 22.9 — one failing figure never blanks its whole widget."""
    return {name: result.label() for name, result in figures.items()}
