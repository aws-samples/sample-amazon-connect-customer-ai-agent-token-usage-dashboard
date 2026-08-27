"""Token reconciliation.

The identity, verified with zero mismatches across the validation corpus::

    usage_total_tokens = usage_input_tokens + usage_output_tokens
                       + cache_read_input_tokens + cache_write_input_tokens

``usage_input_tokens`` is *fresh* input and excludes cache reads. Summing input +
output alone therefore undercounts wherever prompt caching is active — by exactly
4,301 tokens per turn on one validated agent.

A span that fails reconciliation is retained, marked, and still counted in
totals. Only a span with no total at all is excluded, because there is nothing to
count. Every state has a visible counter: silently dropping a record is the
failure mode this module exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import constants as C
from .span_parser import TypedSpan


class ReconciliationState(str, Enum):
    OK = "OK"
    MISMATCH = "MISMATCH"
    SKIPPED_NON_NUMERIC = "SKIPPED_NON_NUMERIC"
    SKIPPED_ABSENT_TOTAL = "SKIPPED_ABSENT_TOTAL"

    @property
    def is_verified(self) -> bool:
        return self is ReconciliationState.OK

    @property
    def counts_toward_totals(self) -> bool:
        """A span with no total cannot contribute to a token total."""
        return self is not ReconciliationState.SKIPPED_ABSENT_TOTAL


@dataclass(frozen=True)
class ReconciliationResult:
    state: ReconciliationState
    span_id: str | None = None
    reported_total: int | None = None
    computed_sum: int | None = None
    non_numeric_fields: tuple[str, ...] = ()

    @property
    def delta(self) -> int | None:
        if self.reported_total is None or self.computed_sum is None:
            return None
        return self.reported_total - self.computed_sum


def reconcile(span: TypedSpan) -> ReconciliationResult:
    """Verify the token identity for one inference span."""
    span_id = span.span_id

    # Any present token field that failed integer typing blocks verification.
    non_numeric = tuple(
        f
        for f in C.TOKEN_FIELDS
        if span.has(f) and span.int_or_none(f) is None
    )
    if non_numeric:
        return ReconciliationResult(
            ReconciliationState.SKIPPED_NON_NUMERIC,
            span_id=span_id,
            non_numeric_fields=non_numeric,
        )

    total = span.int_or_none("usage_total_tokens")
    if total is None:
        return ReconciliationResult(
            ReconciliationState.SKIPPED_ABSENT_TOTAL, span_id=span_id
        )

    # Absent cache fields contribute zero *for verification only*. This is not
    # the same as treating absence as zero elsewhere - see Cache_State.
    computed = (
        (span.int_or_none("usage_input_tokens") or 0)
        + (span.int_or_none("usage_output_tokens") or 0)
        + (span.int_or_none("cache_read_input_tokens") or 0)
        + (span.int_or_none("cache_write_input_tokens") or 0)
    )

    state = (
        ReconciliationState.OK
        if total == computed
        else ReconciliationState.MISMATCH
    )
    return ReconciliationResult(
        state, span_id=span_id, reported_total=total, computed_sum=computed
    )


@dataclass
class ReconciliationTally:
    """Counters the dashboard must display beside any token total."""

    ok: int = 0
    mismatch: int = 0
    skipped_non_numeric: int = 0
    skipped_absent_total: int = 0

    def add(self, result: ReconciliationResult) -> None:
        match result.state:
            case ReconciliationState.OK:
                self.ok += 1
            case ReconciliationState.MISMATCH:
                self.mismatch += 1
            case ReconciliationState.SKIPPED_NON_NUMERIC:
                self.skipped_non_numeric += 1
            case ReconciliationState.SKIPPED_ABSENT_TOTAL:
                self.skipped_absent_total += 1

    @property
    def verified(self) -> int:
        return self.ok

    @property
    def unreconciled_in_totals(self) -> int:
        """Spans included in token totals despite failing verification."""
        return self.mismatch + self.skipped_non_numeric

    @property
    def total_considered(self) -> int:
        return (
            self.ok
            + self.mismatch
            + self.skipped_non_numeric
            + self.skipped_absent_total
        )
