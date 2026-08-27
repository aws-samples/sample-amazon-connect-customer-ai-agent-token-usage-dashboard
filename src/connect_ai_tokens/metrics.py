"""Metric_Publisher — EMF emission with bounded cardinality.

Custom metrics are the largest cost line at Level 1, and the requirement asks for
slicing by seven attributes plus channel. Emitting that cross product is unbounded:
20 agents x 3 versions x 4 models x 5 prompts x 2 use cases x 3 instances x 2
channels is 7,200 dimension-value combinations per metric.

So this emits a **fixed set of five dimension sets** instead. Metric count becomes
``metrics x sum(|Dn|)`` rather than ``metrics x product(|attribute|)`` — linear in
observed values rather than multiplicative. Finer slicing than these five is a
Level 2 Athena capability, where dimensionality costs nothing.

Cache_State is computed here rather than in a query because it depends on field
*presence* across a set of spans, which no metric aggregation can express after
the fact.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

from . import constants as C
from .channel import ChannelInfo
from .reasoning import ReasoningSplit, apportion
from .reconciliation import ReconciliationState, reconcile
from .span_parser import TypedSpan


class CacheState(str, Enum):
    ON = "ON"
    OFF = "OFF"
    MIXED = "MIXED"
    NO_DATA = "no data available"


# --------------------------------------------------------------------------
# Dimension sets - the cardinality control
# --------------------------------------------------------------------------

DIMENSION_SETS: tuple[tuple[str, ...], ...] = (
    ("channel",),
    ("channel", "ai_agent_name", "ai_agent_version"),
    ("channel", "request_model"),
    ("channel", "ai_agent_name", "prompt_version"),
    ("channel", "instance_id"),
)
"""D1..D5. Every set is channel-scoped so no emitted metric can blend channels."""


def dimension_set_names() -> tuple[str, ...]:
    return tuple(f"D{i + 1}" for i in range(len(DIMENSION_SETS)))


@dataclass
class SpanFacts:
    """Everything the publisher needs from one span, already derived."""

    span: TypedSpan
    channel_info: ChannelInfo
    reconciliation: ReconciliationState
    reasoning: ReasoningSplit | None

    def dimension_value(self, name: str) -> str:
        match name:
            case "channel":
                return self.channel_info.channel
            case "instance_id":
                arn = self.span.text("instance_arn") or ""
                return arn.rsplit("/", 1)[-1] or C.DIMENSION_UNKNOWN
            case _:
                return self.span.text(name) or C.DIMENSION_UNKNOWN


def derive_facts(span: TypedSpan, channel_info: ChannelInfo) -> SpanFacts:
    return SpanFacts(
        span=span,
        channel_info=channel_info,
        reconciliation=reconcile(span).state,
        reasoning=apportion(span) if span.is_inference else None,
    )


# --------------------------------------------------------------------------
# Cache_State
# --------------------------------------------------------------------------


def derive_cache_state(spans: Sequence[TypedSpan]) -> CacheState:
    """From field PRESENCE, never from a sum.

    An absent cache field summed gives 0, which is indistinguishable from an
    active cache that got no hits. A cache field present with value 0 counts as
    present.
    """
    inference = [s for s in spans if s.is_inference]
    if not inference:
        return CacheState.NO_DATA
    with_cache = sum(1 for s in inference if s.has_any_cache_field)
    if with_cache == 0:
        return CacheState.OFF
    if with_cache == len(inference):
        return CacheState.ON
    return CacheState.MIXED


def cache_state_by_agent(
    spans: Iterable[TypedSpan],
) -> dict[tuple[str, str], CacheState]:
    """Keyed by (agent name, agent version) — a version can change caching."""
    grouped: dict[tuple[str, str], list[TypedSpan]] = defaultdict(list)
    for s in spans:
        if not s.is_inference:
            continue
        key = (
            s.text("ai_agent_name") or C.DIMENSION_UNKNOWN,
            s.text("ai_agent_version") or C.DIMENSION_UNKNOWN,
        )
        grouped[key].append(s)
    return {k: derive_cache_state(v) for k, v in grouped.items()}


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


@dataclass
class MetricBucket:
    span_count: int = 0
    inference_count: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    spans_with_cache_fields: int = 0
    reasoning_chars: int = 0
    text_chars: int = 0
    tool_chars: int = 0
    reasoning_tokens: float = 0.0
    ttft_values: list[int] = field(default_factory=list)
    duration_values: list[int] = field(default_factory=list)
    barge_in_discarded_tokens: int = 0
    barge_in_spans: int = 0
    output_ceiling_hits: int = 0
    reconciliation_mismatches: int = 0
    unapportioned_spans: int = 0
    contacts: set[str] = field(default_factory=set)

    def add(self, facts: SpanFacts) -> None:
        span = facts.span
        self.span_count += 1
        cid = span.text("initial_contact_id")
        if cid:
            self.contacts.add(cid)

        if not span.is_inference:
            return

        self.inference_count += 1

        if facts.reconciliation is ReconciliationState.MISMATCH:
            self.reconciliation_mismatches += 1

        if facts.reconciliation.counts_toward_totals:
            self.total_tokens += span.int_or_none("usage_total_tokens") or 0
        self.input_tokens += span.int_or_none("usage_input_tokens") or 0
        self.output_tokens += span.int_or_none("usage_output_tokens") or 0
        self.cache_read_tokens += span.int_or_none("cache_read_input_tokens") or 0
        self.cache_write_tokens += span.int_or_none("cache_write_input_tokens") or 0
        if span.has_any_cache_field:
            self.spans_with_cache_fields += 1

        ttft = span.int_or_none("time_to_first_token_ms")
        if ttft is not None:
            self.ttft_values.append(ttft)
        dur = span.duration_ms
        if dur is not None:
            self.duration_values.append(dur)

        if span.is_barge_in_discard:
            self.barge_in_spans += 1
            self.barge_in_discarded_tokens += span.int_or_none("usage_total_tokens") or 0

        out = span.int_or_none("usage_output_tokens")
        ceiling = span.int_or_none("request_max_tokens")
        if out is not None and ceiling:
            if out >= 0.9 * ceiling:
                self.output_ceiling_hits += 1

        if facts.reasoning is None:
            self.unapportioned_spans += 1
        else:
            r = facts.reasoning
            self.reasoning_chars += r.chars_reasoning
            self.text_chars += r.chars_text
            self.tool_chars += r.chars_tool
            self.reasoning_tokens += r.tokens_reasoning

    # -- derived ------------------------------------------------------------
    @property
    def cache_hit_ratio(self) -> float | None:
        denom = self.cache_read_tokens + self.input_tokens
        if self.spans_with_cache_fields == 0 or denom == 0:
            return None
        return self.cache_read_tokens / denom

    @property
    def reasoning_share_incl_tool(self) -> float | None:
        total = self.reasoning_chars + self.text_chars + self.tool_chars
        return self.reasoning_chars / total if total else None

    @property
    def reasoning_share_excl_tool(self) -> float | None:
        denom = self.reasoning_chars + self.text_chars
        return self.reasoning_chars / denom if denom else None

    @property
    def tokens_per_contact(self) -> float | None:
        return self.total_tokens / len(self.contacts) if self.contacts else None

    @property
    def model_time_ms_per_contact(self) -> float | None:
        if not self.contacts or not self.duration_values:
            return None
        return sum(self.duration_values) / len(self.contacts)


class MetricPublisher:
    def __init__(self, namespace: str = "ConnectAI/TokenEfficiency") -> None:
        self.namespace = namespace
        self._buckets: dict[tuple[tuple[str, str], ...], MetricBucket] = defaultdict(
            MetricBucket
        )

    def add(self, facts: SpanFacts) -> None:
        for dims in DIMENSION_SETS:
            key = tuple((d, facts.dimension_value(d)) for d in dims)
            self._buckets[key].add(facts)

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)

    def buckets(self) -> dict[tuple[tuple[str, str], ...], MetricBucket]:
        return dict(self._buckets)

    def emf_records(self) -> list[dict]:
        """One EMF record per dimension-set bucket."""
        records = []
        for key, bucket in self._buckets.items():
            dims = {name: value for name, value in key}
            values: dict[str, float] = {
                "SpanCount": bucket.span_count,
                "InferenceCount": bucket.inference_count,
                "TotalTokens": bucket.total_tokens,
                "InputTokens": bucket.input_tokens,
                "OutputTokens": bucket.output_tokens,
                "CacheReadTokens": bucket.cache_read_tokens,
                "CacheWriteTokens": bucket.cache_write_tokens,
                "ReasoningTokens": round(bucket.reasoning_tokens, 3),
                "BargeInDiscardedTokens": bucket.barge_in_discarded_tokens,
                "OutputCeilingHits": bucket.output_ceiling_hits,
                "ReconciliationMismatches": bucket.reconciliation_mismatches,
                "UnapportionedSpans": bucket.unapportioned_spans,
                "Contacts": len(bucket.contacts),
            }
            if bucket.cache_hit_ratio is not None:
                values["CacheHitRatio"] = round(bucket.cache_hit_ratio, 6)
            if bucket.reasoning_share_incl_tool is not None:
                values["ReasoningShareInclTool"] = round(
                    bucket.reasoning_share_incl_tool, 6
                )
                values["ReasoningShareExclTool"] = round(
                    bucket.reasoning_share_excl_tool or 0.0, 6
                )

            record: dict = {
                "_aws": {
                    "Timestamp": 0,  # set by the caller at flush time
                    "CloudWatchMetrics": [
                        {
                            "Namespace": self.namespace,
                            "Dimensions": [list(dims.keys())],
                            "Metrics": [{"Name": n} for n in values],
                        }
                    ],
                },
                **dims,
                **values,
            }
            if bucket.ttft_values:
                record["TimeToFirstTokenMs"] = bucket.ttft_values
            if bucket.duration_values:
                record["InferenceDurationMs"] = bucket.duration_values
            records.append(record)
        return records

    def serialise(self, timestamp_ms: int) -> list[str]:
        out = []
        for record in self.emf_records():
            record["_aws"]["Timestamp"] = timestamp_ms
            out.append(json.dumps(record, separators=(",", ":")))
        return out
