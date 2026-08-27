"""Build the test fixture corpus from captured Connect Assistant log events.

Redaction contract
------------------
This corpus ships in a public repository, so conversation content is removed.
Redaction is *length-preserving and structure-preserving*:

- letters -> 'x', digits -> '0'
- all punctuation, whitespace, commas, quotes and braces are kept verbatim

That matters for two reasons. Character counts drive the reasoning-share and
prompt-size metrics, so lengths must survive. And commas inside free text are
exactly what the parser's depth-aware scanner has to cope with, so stripping
them would make the fixture easier than reality and weaken the test.

JSON-valued fields are walked so that *keys* survive intact and only leaf string
values are redacted — the reasoning apportioner keys off 'text' / 'reasoning' /
'toolUse', which must remain readable.

Usage:  python build_fixtures.py <capture_dir> <out_dir>
"""

from __future__ import annotations

import json
import pathlib
import sys

# Free-text fields in the span record (raw text, not JSON)
RAW_TEXT_SPAN_FIELDS = ("system_instructions",)
# JSON-valued fields in the span record
JSON_SPAN_FIELDS = ("input_messages", "output_messages")
# Free-text / JSON fields on other event types
RAW_TEXT_EVENT_FIELDS = ("prompt", "completion", "parsed_response", "utterance")
JSON_EVENT_FIELDS = ("values",)


def redact_chars(s: str) -> str:
    out = []
    for ch in s:
        if ch.isalpha():
            out.append("x")
        elif ch.isdigit():
            out.append("0")
        else:
            out.append(ch)
    return "".join(out)


def redact_json_values(node):
    """Redact leaf strings, preserve every key and the structure."""
    if isinstance(node, dict):
        return {k: redact_json_values(v) for k, v in node.items()}
    if isinstance(node, list):
        return [redact_json_values(v) for v in node]
    if isinstance(node, str):
        return redact_chars(node)
    return node


def redact_json_field(raw: str) -> str:
    try:
        return json.dumps(redact_json_values(json.loads(raw)), separators=(",", ":"))
    except Exception:
        # Not parseable JSON - fall back to character redaction so nothing leaks
        return redact_chars(raw)


def split_span(body: str) -> list[tuple[str, str]]:
    """Minimal split for redaction purposes only.

    Deliberately simple and independent of the production parser so a parser bug
    cannot silently corrupt the fixture it is tested against.
    """
    import re

    key_head = re.compile(r"[a-z][a-z0-9_]*=")
    depth = 0
    quoted = False
    escaped = False
    points = []
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
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth = max(0, depth - 1)
        elif (
            ch == ","
            and depth == 0
            and i + 2 < len(body)
            and body[i + 1] == " "
            and key_head.match(body, i + 2)
        ):
            points.append(i)
    parts = []
    prev = 0
    for p in points:
        parts.append(body[prev:p])
        prev = p + 2
    parts.append(body[prev:])
    return [tuple(p.partition("=")[::2]) for p in parts]


def redact_span(span_text: str) -> str:
    t = span_text.strip()
    if not (t.startswith("{") and t.endswith("}")):
        return span_text
    pairs = split_span(t[1:-1])
    out = []
    for key, value in pairs:
        k = key.strip()
        if k in RAW_TEXT_SPAN_FIELDS:
            value = redact_chars(value)
        elif k in JSON_SPAN_FIELDS:
            value = redact_json_field(value)
        out.append(f"{k}={value}")
    return "{" + ", ".join(out) + "}"


def redact_event(ev: dict) -> dict:
    ev = dict(ev)
    if "span" in ev and isinstance(ev["span"], str):
        ev["span"] = redact_span(ev["span"])
    for f in RAW_TEXT_EVENT_FIELDS:
        if isinstance(ev.get(f), str):
            ev[f] = redact_chars(ev[f])
    for f in JSON_EVENT_FIELDS:
        if isinstance(ev.get(f), str):
            ev[f] = redact_json_field(ev[f])
    return ev


def main() -> int:
    capture_dir = pathlib.Path(sys.argv[1])
    out_dir = pathlib.Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    events = []
    for path in sorted(capture_dir.glob("*.json")):
        for message in json.load(open(path)):
            events.append(redact_event(json.loads(message)))

    out = out_dir / "assistant_logs.jsonl"
    with open(out, "w") as fh:
        for ev in events:
            fh.write(json.dumps(ev, separators=(",", ":")) + "\n")

    print(f"wrote {len(events)} redacted events -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
