from __future__ import annotations

import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def events() -> list[dict]:
    path = FIXTURES / "assistant_logs.jsonl"
    return [json.loads(l) for l in open(path) if l.strip()]


@pytest.fixture(scope="session")
def manifest() -> dict:
    return json.load(open(FIXTURES / "expected_aggregates.json"))


@pytest.fixture(scope="session")
def trace_events(events) -> list[dict]:
    return [e for e in events if e["event_type"] == "TRANSCRIPT_AI_AGENT_TRACE"]


@pytest.fixture(scope="session")
def spans(trace_events):
    from connect_ai_tokens.span_parser import parse_span

    return [parse_span(e["span"]) for e in trace_events]


@pytest.fixture(scope="session")
def inference_spans(spans):
    return [s for s in spans if s.is_inference and s.has("usage_total_tokens")]
