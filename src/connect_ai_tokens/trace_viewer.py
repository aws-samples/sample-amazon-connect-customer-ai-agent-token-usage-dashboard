"""Trace Viewer — CloudWatch custom widget Lambda.

Renders the full span tree for one contact, via qconnect:ListSpans. Works for
BOTH voice and chat, closing a genuine Connect gap (the built-in trace viewer is
documented as voice-only).

Returns HTML that CloudWatch renders sandboxed in the custom widget iframe.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import boto3

CONFIGURED_ASSISTANTS = set(
    a.strip()
    for a in os.environ.get("CONFIGURED_ASSISTANTS", "").split(",")
    if a.strip()
)
UUID_PATTERN = re.compile(r"^[0-9a-fA-F\-]{36}$")


def validate_id(value: str, name: str) -> str:
    if not value or not UUID_PATTERN.match(value):
        raise ValueError(f"Invalid {name}: {value!r}")
    return value


def handler(event: dict, context: Any = None) -> str:
    """Custom widget handler.

    event shape (from CloudWatch):
      { "describe": true } — return widget documentation
      { "widgetContext": { "params": { "assistantId": "...", "sessionId": "..." } } }
    """
    if event.get("describe"):
        return _describe()

    params = (event.get("widgetContext") or {}).get("params") or {}
    assistant_id = params.get("assistantId", "")
    session_id = params.get("sessionId", "")

    try:
        validate_id(assistant_id, "assistantId")
        validate_id(session_id, "sessionId")
    except ValueError as e:
        return _error(str(e))

    if CONFIGURED_ASSISTANTS and assistant_id not in CONFIGURED_ASSISTANTS:
        return _error(
            f"Assistant {assistant_id} is not in the configured scope. "
            "This prevents enumeration of assistants outside this deployment."
        )

    client = boto3.client("qconnect")
    try:
        spans = _list_all_spans(client, assistant_id, session_id)
    except Exception as e:
        return _error(f"ListSpans failed: {e}")

    if not spans:
        return _no_spans(session_id)

    return _render_tree(spans, assistant_id, session_id)


def _list_all_spans(client, assistant_id: str, session_id: str) -> list[dict]:
    """Always live, never cached (Requirement 14.5)."""
    spans = []
    kwargs = {"assistantId": assistant_id, "sessionId": session_id}
    while True:
        resp = client.list_spans(**kwargs)
        spans.extend(resp.get("spans", []))
        token = resp.get("nextToken")
        if not token:
            break
        kwargs["nextToken"] = token
    return spans


def _render_tree(spans: list[dict], assistant_id: str, session_id: str) -> str:
    by_parent: dict[str | None, list[dict]] = {}
    for s in spans:
        pid = s.get("parentSpanId")
        by_parent.setdefault(pid, []).append(s)

    ids_in_set = {s["spanId"] for s in spans}
    roots = [s for s in spans if s.get("parentSpanId") not in ids_in_set]

    html = [
        "<style>",
        "body{font-family:monospace;font-size:13px;color:#d4d4d4;background:#1e1e1e;margin:8px}",
        ".span{margin-left:20px;border-left:1px solid #444;padding:4px 0 4px 10px}",
        ".name{font-weight:bold;color:#569cd6} .ok{color:#4ec9b0} .err{color:#f44747}",
        ".tok{color:#ce9178} .dur{color:#b5cea8} .lbl{color:#808080}",
        "</style>",
        f"<div><b>Session:</b> {session_id} &nbsp; <b>Assistant:</b> {assistant_id}</div>",
        f"<div style='margin-bottom:8px'><b>Spans:</b> {len(spans)}</div>",
    ]

    def render(span, depth=0):
        attrs = span.get("attributes", {})
        name = span.get("spanName", "?")
        status = span.get("status", "?")
        status_cls = "ok" if status == "OK" else "err"

        # Duration
        start = span.get("startTimestamp", "")
        end = span.get("endTimestamp", "")
        dur = ""
        if start and end:
            try:
                from datetime import datetime

                t0 = datetime.fromisoformat(start.replace("+00:00", "+00:00"))
                t1 = datetime.fromisoformat(end.replace("+00:00", "+00:00"))
                ms = int((t1 - t0).total_seconds() * 1000)
                dur = f"{ms:,}ms"
            except Exception:
                dur = ""

        tokens = attrs.get("usageTotalTokens")
        tok_str = f" <span class='tok'>{tokens:,} tok</span>" if tokens else ""
        ttft = attrs.get("timeToFirstTokenMs")
        ttft_str = f" <span class='lbl'>TTFT {ttft}ms</span>" if ttft else ""
        dur_str = f" <span class='dur'>{dur}</span>" if dur else ""
        status_desc = span.get("statusDescription") or ""
        desc_str = f" <span class='err'>({status_desc})</span>" if status_desc else ""

        html.append(
            f"<div class='span'>"
            f"<span class='name'>{name}</span> "
            f"<span class='{status_cls}'>[{status}]</span>"
            f"{dur_str}{tok_str}{ttft_str}{desc_str}"
            f"</div>"
        )
        for child in by_parent.get(span["spanId"], []):
            html.append("<div class='span'>")
            render(child, depth + 1)
            html.append("</div>")

    for root in roots:
        render(root)

    return "\n".join(html)


def _describe() -> str:
    return json.dumps({
        "markdown": (
            "## AI Agent Trace Viewer\n"
            "Shows the full span tree for one contact via `qconnect:ListSpans`.\n\n"
            "Works for **both voice and chat** — Connect's built-in trace viewer "
            "is voice-only.\n\n"
            "Parameters: `assistantId`, `sessionId`"
        ),
        "decorate": True,
        "displayName": "AI Agent Trace Viewer",
    })


def _error(msg: str) -> str:
    return f"<div style='color:#f44747;font-family:monospace;padding:12px'><b>Error:</b> {msg}</div>"


def _no_spans(session_id: str) -> str:
    return (
        f"<div style='color:#dcdcaa;font-family:monospace;padding:12px'>"
        f"<b>No spans returned</b> for session {session_id}.<br>"
        f"The session may be too old or may not have generated AI agent spans."
        f"</div>"
    )
