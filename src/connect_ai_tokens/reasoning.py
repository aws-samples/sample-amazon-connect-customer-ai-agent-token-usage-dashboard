"""Reasoning apportionment.

Roughly half of everything these agents generate is reasoning that is not
surfaced in the response. At ~9.25 ms per output token that still adds to
response delay, and no Connect metric or data lake column exposes it —
``output_token`` is a single total.

Method (same-grain)
-------------------
``output_messages`` on the inference span already carries typed ``text``,
``reasoning`` and ``toolUse`` values *in the same record* that reports
``usage_output_tokens``. Count characters per type within the span, then
apportion that span's own token count by the character share.

This supersedes an earlier method that took character counts from
``TRANSCRIPT_ORCHESTRATION_MESSAGE`` events and applied them to span token
counts. Those are overlapping but non-identical populations, and mixing them
produced a figure (49.4%) that could not be tied to a real denominator.

Two denominators, and they differ by 7.5 points
-----------------------------------------------
- ``share_incl_tool``  = reasoning / (text + reasoning + toolUse)  -> 43.7%
- ``share_excl_tool``  = reasoning / (text + reasoning)            -> 51.2%

Neither may be rendered without stating which one it is. Enforced here rather
than left to the widget, because "half the output is reasoning" is a claim that
changes meaning depending on whether tool payloads are counted.

Distribution matters as much as the headline
--------------------------------------------
Per span the share ranges 0% to 82.2%, median 47.5%, and 39 of 132 validated
spans contained no reasoning at all. A headline figure alone is misleading, so
``ReasoningTally`` carries the per-span distribution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .span_parser import TypedSpan


@dataclass(frozen=True)
class ReasoningSplit:
    """Character and apportioned-token split for one inference span."""

    chars_text: int
    chars_reasoning: int
    chars_tool: int
    tokens_text: float
    tokens_reasoning: float
    tokens_tool: float
    output_tokens: int

    @property
    def chars_total(self) -> int:
        return self.chars_text + self.chars_reasoning + self.chars_tool

    @property
    def share_incl_tool(self) -> float:
        """Reasoning as a share of ALL generated output, tool payload included."""
        return self.chars_reasoning / self.chars_total if self.chars_total else 0.0

    @property
    def share_excl_tool(self) -> float:
        """Reasoning as a share of prose only, tool payload excluded."""
        denom = self.chars_text + self.chars_reasoning
        return self.chars_reasoning / denom if denom else 0.0

    def reasoning_ms(self, ms_per_output_token: float) -> float:
        """Generation time spent on output that is not surfaced in the response."""
        return self.tokens_reasoning * ms_per_output_token


UNAPPORTIONED: ReasoningSplit | None = None
"""Returned when the split cannot be computed. Never silently zero."""


def apportion(span: TypedSpan) -> ReasoningSplit | None:
    """Split one inference span's output tokens by value type.

    Returns None (UNAPPORTIONED) when ``output_messages`` is absent, unparseable
    or empty, or when ``usage_output_tokens`` is absent. A span that genuinely
    generated no reasoning returns a split with ``chars_reasoning == 0`` — that
    is a real zero and is distinct from None.
    """
    raw = span.text("output_messages")
    if not raw:
        return None

    out_tokens = span.int_or_none("usage_output_tokens")
    if out_tokens is None:
        return None

    try:
        messages = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(messages, list):
        return None

    c_text = c_reasoning = c_tool = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        for value in message.get("values", []) or []:
            if not isinstance(value, dict):
                continue
            if "text" in value:
                c_text += len(str((value["text"] or {}).get("value", "")))
            elif "reasoning" in value:
                c_reasoning += len(str((value["reasoning"] or {}).get("value", "")))
            elif "toolUse" in value:
                c_tool += len(json.dumps(value["toolUse"], separators=(",", ":")))

    chars_total = c_text + c_reasoning + c_tool
    if chars_total == 0:
        return None

    # Fractional on purpose. Summed before rounding so per-agent totals do not
    # drift from the true usage_output_tokens sum (Property 4).
    return ReasoningSplit(
        chars_text=c_text,
        chars_reasoning=c_reasoning,
        chars_tool=c_tool,
        tokens_text=out_tokens * c_text / chars_total,
        tokens_reasoning=out_tokens * c_reasoning / chars_total,
        tokens_tool=out_tokens * c_tool / chars_total,
        output_tokens=out_tokens,
    )


@dataclass
class ReasoningTally:
    """Aggregate split plus the distribution the headline figure needs."""

    chars_text: int = 0
    chars_reasoning: int = 0
    chars_tool: int = 0
    tokens_text: float = 0.0
    tokens_reasoning: float = 0.0
    tokens_tool: float = 0.0
    output_tokens: int = 0
    apportioned_spans: int = 0
    unapportioned_spans: int = 0
    zero_reasoning_spans: int = 0
    per_span_shares: list[float] = field(default_factory=list)

    def add(self, split: ReasoningSplit | None) -> None:
        if split is None:
            self.unapportioned_spans += 1
            return
        self.apportioned_spans += 1
        self.chars_text += split.chars_text
        self.chars_reasoning += split.chars_reasoning
        self.chars_tool += split.chars_tool
        self.tokens_text += split.tokens_text
        self.tokens_reasoning += split.tokens_reasoning
        self.tokens_tool += split.tokens_tool
        self.output_tokens += split.output_tokens
        self.per_span_shares.append(split.share_incl_tool)
        if split.chars_reasoning == 0:
            self.zero_reasoning_spans += 1

    @property
    def chars_total(self) -> int:
        return self.chars_text + self.chars_reasoning + self.chars_tool

    @property
    def share_incl_tool(self) -> float:
        return self.chars_reasoning / self.chars_total if self.chars_total else 0.0

    @property
    def share_excl_tool(self) -> float:
        denom = self.chars_text + self.chars_reasoning
        return self.chars_reasoning / denom if denom else 0.0

    @property
    def chars_per_output_token(self) -> float:
        return self.chars_total / self.output_tokens if self.output_tokens else 0.0

    def distribution(self) -> dict[str, float]:
        """Median, min and max per-span share. A headline needs this beside it."""
        if not self.per_span_shares:
            return {}
        s = sorted(self.per_span_shares)
        mid = len(s) // 2
        median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
        return {
            "median": median,
            "mean": sum(s) / len(s),
            "min": s[0],
            "max": s[-1],
            "n": len(s),
            "zero_reasoning_spans": self.zero_reasoning_spans,
        }
