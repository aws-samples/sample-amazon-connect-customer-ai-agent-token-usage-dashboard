"""Span_Parser and Span_Serializer.

The ``span`` field of a Connect Assistant log event is documented as
"JSON-serialized map". It is not. It is a Java ``toString`` map::

    {span_id=abc, span_name=inference, response_finish_reasons=["end_turn"],
     status_description=Abandoning due to barge-in, usage_input_tokens=5823}

Unquoted keys, unquoted values, values containing commas, values containing
nested JSON, values containing free text with spaces. ``json.loads`` fails on it.

Splitting naively on ``", "`` corrupts any value containing a comma. This module
splits only at positions that are simultaneously

1. at bracket depth zero,
2. outside any quoted string, and
3. immediately followed by a snake_case key and ``=``.

Condition 3 is the load-bearing guard: free text containing ``, foo=`` only splits
if ``foo`` is a valid snake_case identifier directly followed by ``=`` — the same
shape a real key has. That residual ambiguity is why the round-trip property
(``parse(serialize(parse(x))) == parse(x)``) exists as a regression test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final, Iterator, Mapping

from . import constants as C


class _Absent:
    """Sentinel for a field that is not present on the span.

    Distinct from 0 and from None. This distinction is not cosmetic: the cache
    token fields are *absent* when prompt caching is inactive, not zero. A SUM
    over an absent field returns 0, which is indistinguishable from an active
    cache that happened to get no hits. Cache_State is only derivable because
    absence is preserved here.
    """

    _instance: "_Absent | None" = None

    def __new__(cls) -> "_Absent":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "ABSENT"

    def __eq__(self, other: object) -> bool:
        return other is self

    def __hash__(self) -> int:
        return hash("<ABSENT>")


ABSENT: Final[_Absent] = _Absent()


class SpanParseError(ValueError):
    """Raised when a span record cannot be parsed. Caller counts and continues."""


KNOWN_SPAN_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "span_id", "parent_span_id", "span_name", "span_type",
        "start_timestamp", "end_timestamp", "status", "status_description",
        "error_type", "origin_request_id", "request_id", "operation_name",
        "provider_name", "instance_arn", "contact_id", "initial_contact_id",
        "session_name", "ai_agent_arn", "ai_agent_id", "ai_agent_type",
        "ai_agent_name", "ai_agent_version", "ai_agent_orchestrator_use_case",
        "request_model", "request_max_tokens", "temperature",
        "response_finish_reasons", "usage_input_tokens", "usage_output_tokens",
        "usage_total_tokens", "cache_read_input_tokens",
        "cache_write_input_tokens", "prompt_arn", "prompt_id", "prompt_type",
        "prompt_name", "prompt_version", "time_to_first_token_ms",
        "input_messages", "output_messages", "system_instructions",
        "input_messages_truncated", "guardrail_assessments",
        # Added by the service after the original 43-field survey. Discovered by
        # the unknown-field canary test, which is exactly what it is for.
        "trace_id",
    }
)
"""Span field names observed in production.

The original survey found 43 across 296 spans; ``trace_id`` was added by the
service afterwards and found by the canary test. An unknown key is retained
rather than treated as a failure (Requirement 2.10) — the service can add fields
and this parser must not start rejecting records because of it.
"""

_INT_FIELD_SUFFIXES: Final[tuple[str, ...]] = ("_ms", "_timestamp")
_INT_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    set(C.TOKEN_FIELDS) | {"request_max_tokens", "orchestration_iteration"}
)
"""Fields typed as integers when their value is all digits.

Version fields are deliberately excluded: ``ai_agent_version`` and
``prompt_version`` can carry ``$LATEST``, so they stay text to keep one type per
field rather than a union that every caller has to handle.
"""

_KEY_HEAD: Final[re.Pattern[str]] = re.compile(r"[a-z][a-z0-9_]*=")
_ALL_DIGITS: Final[re.Pattern[str]] = re.compile(r"\A\d+\Z")

_OPENERS: Final[str] = "[{("
_CLOSERS: Final[str] = "]})"


@dataclass(frozen=True)
class TypedSpan:
    """A parsed span. Absent fields are absent from ``fields``, never zero."""

    fields: Mapping[str, int | str]
    unknown_fields: Mapping[str, int | str] = field(default_factory=dict)

    # -- access -------------------------------------------------------------
    def get(self, key: str) -> int | str | _Absent:
        if key in self.fields:
            return self.fields[key]
        if key in self.unknown_fields:
            return self.unknown_fields[key]
        return ABSENT

    def has(self, key: str) -> bool:
        return key in self.fields or key in self.unknown_fields

    def is_absent(self, key: str) -> bool:
        return not self.has(key)

    def int_or_none(self, key: str) -> int | None:
        """Integer value, or None when absent or not integer-typed."""
        v = self.get(key)
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    def text(self, key: str) -> str | None:
        v = self.get(key)
        return v if isinstance(v, str) else None

    def keys(self) -> Iterator[str]:
        yield from self.fields
        yield from self.unknown_fields

    # -- common projections -------------------------------------------------
    @property
    def span_name(self) -> str | None:
        return self.text("span_name")

    @property
    def span_id(self) -> str | None:
        return self.text("span_id")

    @property
    def is_inference(self) -> bool:
        return self.span_name == C.SPAN_INFERENCE

    @property
    def duration_ms(self) -> int | None:
        start = self.int_or_none("start_timestamp")
        end = self.int_or_none("end_timestamp")
        return end - start if start is not None and end is not None else None

    @property
    def has_any_cache_field(self) -> bool:
        """True when the span reports prompt caching at all.

        Presence, not value. A cache field present with value 0 counts as
        present; a cache field absent means caching was inactive.
        """
        return any(self.has(f) for f in C.CACHE_FIELDS)

    @property
    def is_barge_in_discard(self) -> bool:
        """Inference that was paid for and thrown away because the caller spoke.

        Distinct from a genuine failure — this is why error_type is retained
        rather than collapsed into a success boolean.
        """
        return (
            self.is_inference
            and self.text("status") == "ERROR"
            and self.text("error_type") == "barge_in"
        )


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _typed(key: str, raw: str) -> int | str:
    """Type a value.

    Token fields, ``*_ms`` and ``*_timestamp`` become int when all digits.
    Anything else, including a token field carrying a non-numeric value, stays
    text — reconciliation then reports it rather than crashing on it.
    """
    if key in _INT_FIELD_NAMES or key.endswith(_INT_FIELD_SUFFIXES):
        if _ALL_DIGITS.match(raw):
            return int(raw)
    return raw


def _find_split_points(body: str) -> tuple[list[int], int]:
    """Return (split indices, final bracket depth).

    A non-zero final depth means unbalanced brackets, which the caller treats as
    a parse failure.
    """
    points: list[int] = []
    depth = 0
    max_depth = 0
    quoted = False
    escaped = False
    n = len(body)

    for i, ch in enumerate(body):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            quoted = not quoted
            continue
        if quoted:
            continue

        if ch in _OPENERS:
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch in _CLOSERS:
            # Clamp rather than go negative: a stray closer inside unquoted free
            # text should not corrupt the scan for every later field.
            depth = max(0, depth - 1)
        elif (
            ch == ","
            and depth == 0
            and i + 2 < n
            and body[i + 1] == " "
            and _KEY_HEAD.match(body, i + 2)
        ):
            points.append(i)

    if max_depth > C.MAX_BRACKET_DEPTH:
        raise SpanParseError(f"bracket depth {max_depth} exceeds {C.MAX_BRACKET_DEPTH}")
    return points, depth


def parse_span(raw: Any) -> TypedSpan:
    """Parse a raw span string into a TypedSpan.

    Raises SpanParseError on absent, empty, oversized, brace-unbalanced, or
    otherwise unparseable input (Requirement 2.11).
    """
    if raw is None or not isinstance(raw, str):
        raise SpanParseError("span field absent or not a string")

    text = raw.strip()
    if not text:
        raise SpanParseError("span field empty")
    if len(text) > C.MAX_SPAN_CHARS:
        raise SpanParseError(f"span length {len(text)} exceeds {C.MAX_SPAN_CHARS}")
    if not (text.startswith("{") and text.endswith("}")):
        raise SpanParseError("span not brace-delimited")

    body = text[1:-1]
    if not body.strip():
        raise SpanParseError("span body empty")

    points, final_depth = _find_split_points(body)
    if final_depth != 0:
        raise SpanParseError("unbalanced brackets in span body")

    parts: list[str] = []
    prev = 0
    for p in points:
        parts.append(body[prev:p])
        prev = p + 2  # skip ", "
    parts.append(body[prev:])

    if len(parts) > C.MAX_SPAN_KEYS:
        raise SpanParseError(f"{len(parts)} keys exceeds {C.MAX_SPAN_KEYS}")

    known: dict[str, int | str] = {}
    unknown: dict[str, int | str] = {}
    for part in parts:
        if "=" not in part:
            # A fragment with no assignment means the split heuristic misfired
            # or the record is malformed. Either way it is not silently dropped.
            raise SpanParseError(f"fragment without '=': {part[:40]!r}")
        key, _, value = part.partition("=")
        key = key.strip()
        if not key:
            raise SpanParseError("empty key")
        target = known if key in KNOWN_SPAN_FIELDS else unknown
        target[key] = _typed(key, value)

    if not known and not unknown:
        raise SpanParseError("no fields parsed")

    return TypedSpan(fields=known, unknown_fields=unknown)


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------


def serialize_span(span: TypedSpan) -> str:
    """Render a TypedSpan back to Java ``toString`` map form.

    Absent fields are omitted. Byte-identity with the original input is NOT a
    goal — key order and spacing may differ. What must hold is that reparsing
    yields an equal TypedSpan (Requirement 2.6, Property 3).
    """
    items = [f"{k}={v}" for k, v in span.fields.items()]
    items += [f"{k}={v}" for k, v in span.unknown_fields.items()]
    return "{" + ", ".join(items) + "}"


def parse_event(event: Mapping[str, Any]) -> TypedSpan:
    """Parse the span out of a full Assistant_Log event."""
    if event.get("event_type") != C.EVENT_AI_AGENT_TRACE:
        raise SpanParseError(f"event_type is {event.get('event_type')!r}")
    return parse_span(event.get("span"))
