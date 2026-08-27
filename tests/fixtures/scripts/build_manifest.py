"""Compute the expected-aggregates manifest from the fixture corpus.

Every later task asserts against this file. It is generated rather than
hand-written so it cannot drift from the fixture it describes.

Usage:  python build_manifest.py <fixture_dir>
"""

from __future__ import annotations

import json
import pathlib
import statistics as st
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src"))

from connect_ai_tokens import constants as C  # noqa: E402
from connect_ai_tokens.reasoning import ReasoningTally, apportion  # noqa: E402
from connect_ai_tokens.reconciliation import (  # noqa: E402
    ReconciliationTally,
    reconcile,
)
from connect_ai_tokens.span_parser import SpanParseError, parse_span  # noqa: E402


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(q * len(s)), len(s) - 1)
    return s[idx]


def main() -> int:
    fixture_dir = pathlib.Path(sys.argv[1])
    events = [
        json.loads(line)
        for line in open(fixture_dir / "assistant_logs.jsonl")
        if line.strip()
    ]

    event_types = Counter(e["event_type"] for e in events)
    traces = [e for e in events if e["event_type"] == C.EVENT_AI_AGENT_TRACE]

    parse_failures = 0
    spans = []
    for e in traces:
        try:
            spans.append(parse_span(e["span"]))
        except SpanParseError:
            parse_failures += 1

    inference = [s for s in spans if s.is_inference and s.has("usage_total_tokens")]

    recon = ReconciliationTally()
    for s in inference:
        recon.add(reconcile(s))

    total_tokens = sum(s.int_or_none("usage_total_tokens") or 0 for s in inference)
    input_tokens = sum(s.int_or_none("usage_input_tokens") or 0 for s in inference)
    output_tokens = sum(s.int_or_none("usage_output_tokens") or 0 for s in inference)
    cache_read = sum(s.int_or_none("cache_read_input_tokens") or 0 for s in inference)
    cache_write = sum(s.int_or_none("cache_write_input_tokens") or 0 for s in inference)

    # Cache state by agent, from field PRESENCE
    by_agent: dict[str, list] = defaultdict(list)
    for s in inference:
        by_agent[s.text("ai_agent_name") or C.DIMENSION_UNKNOWN].append(s)

    cache_state = {}
    uncached_tokens = 0
    for agent, group in by_agent.items():
        with_cache = sum(1 for s in group if s.has_any_cache_field)
        if with_cache == 0:
            state = "OFF"
            uncached_tokens += sum(
                s.int_or_none("usage_total_tokens") or 0 for s in group
            )
        elif with_cache == len(group):
            state = "ON"
        else:
            state = "MIXED"
        cache_state[agent] = {
            "state": state,
            "spans": len(group),
            "tokens": sum(s.int_or_none("usage_total_tokens") or 0 for s in group),
        }

    # Per-contact
    by_contact: dict[str, int] = defaultdict(int)
    for s in inference:
        cid = s.text("initial_contact_id")
        if cid:
            by_contact[cid] += s.int_or_none("usage_total_tokens") or 0
    contact_totals = sorted(by_contact.values())

    ttft = [
        v
        for v in (s.int_or_none("time_to_first_token_ms") for s in inference)
        if v is not None
    ]

    reasoning = ReasoningTally()
    for s in inference:
        reasoning.add(apportion(s))

    span_names = Counter(s.span_name for s in spans)
    models = Counter(
        s.text("request_model") for s in inference if s.has("request_model")
    )
    finish = Counter(
        s.text("response_finish_reasons")
        for s in inference
        if s.has("response_finish_reasons")
    )

    manifest = {
        "provenance": {
            "note": (
                "Captured from a live account after CloudWatch Logs retention "
                "(14 days on two of three assistant log groups, 30 on the third) "
                "had already expired part of the original analysis window. "
                "DESIGN.md records the fuller original capture of 1,205 events / "
                "132 token-bearing spans; this manifest describes THIS fixture."
            ),
            "redaction": (
                "Length-preserving and structure-preserving. Letters -> x, "
                "digits -> 0, punctuation kept. JSON keys preserved, leaf string "
                "values redacted."
            ),
            "log_retention_days": {"two_groups": 14, "one_group": 30},
        },
        "corpus": {
            "events": len(events),
            "event_types": dict(event_types),
            "trace_events": len(traces),
            "spans_parsed": len(spans),
            "parse_failures": parse_failures,
            "span_names": {k: v for k, v in span_names.items() if k},
            "token_bearing_inference_spans": len(inference),
        },
        "reconciliation": {
            "ok": recon.ok,
            "mismatch": recon.mismatch,
            "skipped_non_numeric": recon.skipped_non_numeric,
            "skipped_absent_total": recon.skipped_absent_total,
        },
        "tokens": {
            "total": total_tokens,
            "input_fresh": input_tokens,
            "output": output_tokens,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "identity_holds": total_tokens
            == input_tokens + output_tokens + cache_read + cache_write,
            "input_share": round(input_tokens / total_tokens, 4) if total_tokens else 0,
            "output_share": round(output_tokens / total_tokens, 4)
            if total_tokens
            else 0,
            "cache_share": round((cache_read + cache_write) / total_tokens, 4)
            if total_tokens
            else 0,
        },
        "cache": {
            "state_by_agent": cache_state,
            "agents_off": sum(
                1 for v in cache_state.values() if v["state"] == "OFF"
            ),
            "agents_total": len(cache_state),
            "uncached_token_share": round(uncached_tokens / total_tokens, 4)
            if total_tokens
            else 0,
            "hit_ratio_where_active": round(
                cache_read / (cache_read + input_tokens), 4
            )
            if (cache_read + input_tokens)
            else 0,
        },
        "per_contact": {
            "contacts": len(contact_totals),
            "median_tokens": st.median(contact_totals) if contact_totals else 0,
            "p90_tokens": pct([float(v) for v in contact_totals], 0.90),
            "max_tokens": max(contact_totals) if contact_totals else 0,
            "inference_calls_per_contact": round(
                len(inference) / len(contact_totals), 2
            )
            if contact_totals
            else 0,
        },
        "latency": {
            "ttft_p50": st.median(ttft) if ttft else 0,
            "ttft_p90": pct([float(v) for v in ttft], 0.90),
            "ttft_p99": pct([float(v) for v in ttft], 0.99),
            "ttft_n": len(ttft),
        },
        "reasoning": {
            "chars_text": reasoning.chars_text,
            "chars_reasoning": reasoning.chars_reasoning,
            "chars_tool": reasoning.chars_tool,
            "share_incl_tool": round(reasoning.share_incl_tool, 4),
            "share_excl_tool": round(reasoning.share_excl_tool, 4),
            "chars_per_output_token": round(reasoning.chars_per_output_token, 2),
            "tokens_reasoning": round(reasoning.tokens_reasoning, 1),
            "tokens_text": round(reasoning.tokens_text, 1),
            "tokens_tool": round(reasoning.tokens_tool, 1),
            "apportioned_spans": reasoning.apportioned_spans,
            "unapportioned_spans": reasoning.unapportioned_spans,
            "zero_reasoning_spans": reasoning.zero_reasoning_spans,
            "distribution": {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in reasoning.distribution().items()
            },
        },
        "attribution": {
            "models": {k: v for k, v in models.items() if k},
            "finish_reasons": {k: v for k, v in finish.items() if k},
        },
    }

    out = fixture_dir / "expected_aggregates.json"
    with open(out, "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=False)
    print(json.dumps(manifest, indent=2)[:3000])
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
