"""CloudWatch Dashboard definition.

Widgets cover ONLY the validated gaps — metrics that exist nowhere in the OOTB
dashboard, GetMetricDataV2, or the analytics data lake.

All metrics are emitted with dimensions (minimum: channel). The dashboard uses
the D1 dimension set (channel only) for top-level widgets so they aggregate
across agents/models but stay channel-scoped.
"""

from __future__ import annotations

import json

NAMESPACE = "ConnectAI/TokenEfficiency"
PERIOD = 3600  # 1 hour for dashboard visibility


def _dim_metric(name: str, channel: str, **kwargs) -> list:
    """Metric reference with the channel dimension (D1 set)."""
    m = [NAMESPACE, name, "channel", channel]
    if kwargs:
        m.append(kwargs)
    return m


def build_dashboard_body(region: str) -> str:
    """Return the full dashboard JSON body."""
    widgets = []
    y = 0

    # -- Header ---------------------------------------------------------------
    widgets.append({
        "type": "text",
        "x": 0, "y": y, "width": 24, "height": 2,
        "properties": {
            "markdown": (
                "# Connect AI Agent — Cache, Reasoning & TTFT Efficiency\n"
                "These metrics cover gaps not available in the OOTB AI Agent "
                "Performance dashboard or the analytics data lake. "
                "All metrics are channel-scoped (VOICE / CHAT shown separately)."
            ),
        },
    })
    y += 2

    # -- Tab 1: Token economics (gap metrics only) ----------------------------
    widgets.append({
        "type": "text",
        "x": 0, "y": y, "width": 24, "height": 1,
        "properties": {"markdown": "## Token Economics (Gap Metrics)"},
    })
    y += 1

    # Tokens per contact (uses TotalTokens / Contacts as math)
    widgets.append({
        "type": "metric",
        "x": 0, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [{"expression": "m1/m2", "label": "VOICE Tokens/Contact", "id": "e1"}],
                [{"expression": "m3/m4", "label": "CHAT Tokens/Contact", "id": "e2"}],
                [NAMESPACE, "TotalTokens", "channel", "VOICE", {"id": "m1", "visible": False}],
                [NAMESPACE, "Contacts", "channel", "VOICE", {"id": "m2", "visible": False}],
                [NAMESPACE, "TotalTokens", "channel", "CHAT", {"id": "m3", "visible": False}],
                [NAMESPACE, "Contacts", "channel", "CHAT", {"id": "m4", "visible": False}],
            ],
            "stat": "Sum",
            "period": PERIOD,
            "region": region,
            "title": "Total Tokens per Contact (incl cache)",
            "view": "timeSeries",
        },
    })

    # TTFT p50 / p90
    widgets.append({
        "type": "metric",
        "x": 8, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "TimeToFirstTokenMs", "channel", "VOICE", {"stat": "p50", "label": "VOICE p50"}],
                [NAMESPACE, "TimeToFirstTokenMs", "channel", "VOICE", {"stat": "p90", "label": "VOICE p90"}],
                [NAMESPACE, "TimeToFirstTokenMs", "channel", "CHAT", {"stat": "p50", "label": "CHAT p50"}],
                [NAMESPACE, "TimeToFirstTokenMs", "channel", "CHAT", {"stat": "p90", "label": "CHAT p90"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Time to First Token (Response Start)",
            "view": "timeSeries",
            "yAxis": {"left": {"label": "ms"}},
            "annotations": {
                "horizontal": [
                    {"label": "3s responsiveness target", "value": 3000, "color": "#d13212"},
                ],
            },
        },
    })

    # Barge-in waste
    widgets.append({
        "type": "metric",
        "x": 16, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "BargeInDiscardedTokens", "channel", "VOICE", {"stat": "Sum", "label": "VOICE discarded tokens"}],
                [NAMESPACE, "BargeInDiscardedTokens", "channel", "CHAT", {"stat": "Sum", "label": "CHAT discarded tokens"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Barge-In Token Waste",
            "view": "timeSeries",
        },
    })
    y += 6

    # -- Tab 2: Cache economics -----------------------------------------------
    widgets.append({
        "type": "text",
        "x": 0, "y": y, "width": 24, "height": 1,
        "properties": {"markdown": "## Cache Economics (Log-Only — No Data Lake Equivalent)"},
    })
    y += 1

    # Cache hit ratio
    widgets.append({
        "type": "metric",
        "x": 0, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "CacheHitRatio", "channel", "VOICE", {"stat": "Average", "label": "VOICE"}],
                [NAMESPACE, "CacheHitRatio", "channel", "CHAT", {"stat": "Average", "label": "CHAT"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Cache Hit Ratio (where caching active)",
            "view": "timeSeries",
            "yAxis": {"left": {"min": 0, "max": 1, "label": "ratio"}},
        },
    })

    # Cache read vs write tokens
    widgets.append({
        "type": "metric",
        "x": 8, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "CacheReadTokens", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Cache Read"}],
                [NAMESPACE, "CacheWriteTokens", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Cache Write"}],
                [NAMESPACE, "InputTokens", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Fresh Input"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Token Composition — VOICE (Cache vs Fresh)",
            "view": "stacked",
        },
    })

    # Total vs cached
    widgets.append({
        "type": "metric",
        "x": 16, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "TotalTokens", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Total"}],
                [NAMESPACE, "TotalTokens", "channel", "CHAT", {"stat": "Sum", "label": "CHAT Total"}],
                [NAMESPACE, "CacheReadTokens", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Cached"}],
                [NAMESPACE, "CacheReadTokens", "channel", "CHAT", {"stat": "Sum", "label": "CHAT Cached"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Total vs Cached Tokens",
            "view": "timeSeries",
        },
    })
    y += 6

    # -- Tab 3: Reasoning efficiency ------------------------------------------
    widgets.append({
        "type": "text",
        "x": 0, "y": y, "width": 24, "height": 1,
        "properties": {"markdown": "## Reasoning Efficiency (Log-Only — output_token is one total in data lake)"},
    })
    y += 1

    # Reasoning share
    widgets.append({
        "type": "metric",
        "x": 0, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "ReasoningShareInclTool", "channel", "VOICE", {"stat": "Average", "label": "VOICE incl tool"}],
                [NAMESPACE, "ReasoningShareExclTool", "channel", "VOICE", {"stat": "Average", "label": "VOICE excl tool"}],
                [NAMESPACE, "ReasoningShareInclTool", "channel", "CHAT", {"stat": "Average", "label": "CHAT incl tool"}],
                [NAMESPACE, "ReasoningShareExclTool", "channel", "CHAT", {"stat": "Average", "label": "CHAT excl tool"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Reasoning Share of Output",
            "view": "timeSeries",
            "yAxis": {"left": {"min": 0, "max": 1, "label": "share"}},
        },
    })

    # Reasoning tokens (absolute)
    widgets.append({
        "type": "metric",
        "x": 8, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "ReasoningTokens", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Reasoning"}],
                [NAMESPACE, "OutputTokens", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Total Output"}],
                [NAMESPACE, "ReasoningTokens", "channel", "CHAT", {"stat": "Sum", "label": "CHAT Reasoning"}],
                [NAMESPACE, "OutputTokens", "channel", "CHAT", {"stat": "Sum", "label": "CHAT Total Output"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Reasoning vs Total Output Tokens",
            "view": "timeSeries",
        },
    })

    # Output ceiling hits
    widgets.append({
        "type": "metric",
        "x": 16, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "OutputCeilingHits", "channel", "VOICE", {"stat": "Sum", "label": "VOICE"}],
                [NAMESPACE, "OutputCeilingHits", "channel", "CHAT", {"stat": "Sum", "label": "CHAT"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Output Ceiling Hits (>=90% max_tokens)",
            "view": "singleValue",
        },
    })
    y += 6

    # -- Tab 4: Operational health --------------------------------------------
    widgets.append({
        "type": "text",
        "x": 0, "y": y, "width": 24, "height": 1,
        "properties": {"markdown": "## Operational Health"},
    })
    y += 1

    # Reconciliation mismatches
    widgets.append({
        "type": "metric",
        "x": 0, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "ReconciliationMismatches", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Mismatches"}],
                [NAMESPACE, "ReconciliationMismatches", "channel", "CHAT", {"stat": "Sum", "label": "CHAT Mismatches"}],
                [NAMESPACE, "UnapportionedSpans", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Unapportioned"}],
                [NAMESPACE, "UnapportionedSpans", "channel", "CHAT", {"stat": "Sum", "label": "CHAT Unapportioned"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Data Quality: Reconciliation & Apportionment",
            "view": "timeSeries",
        },
    })

    # Decode speed coefficient monitor
    widgets.append({
        "type": "metric",
        "x": 8, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [{"expression": "m1/m2", "label": "VOICE ms/output token", "id": "e1"}],
                [{"expression": "m3/m4", "label": "CHAT ms/output token", "id": "e2"}],
                [NAMESPACE, "InferenceDurationMs", "channel", "VOICE", {"stat": "Sum", "id": "m1", "visible": False}],
                [NAMESPACE, "OutputTokens", "channel", "VOICE", {"stat": "Sum", "id": "m2", "visible": False}],
                [NAMESPACE, "InferenceDurationMs", "channel", "CHAT", {"stat": "Sum", "id": "m3", "visible": False}],
                [NAMESPACE, "OutputTokens", "channel", "CHAT", {"stat": "Sum", "id": "m4", "visible": False}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Decode Speed (ms/output token) — Coefficient Monitor",
            "view": "timeSeries",
            "yAxis": {"left": {"label": "ms/token"}},
            "annotations": {
                "horizontal": [
                    {"label": "Calibrated: 9.25", "value": 9.25, "color": "#1f77b4"},
                ],
            },
        },
    })

    # Pipeline throughput
    widgets.append({
        "type": "metric",
        "x": 16, "y": y, "width": 8, "height": 6,
        "properties": {
            "metrics": [
                [NAMESPACE, "SpanCount", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Spans"}],
                [NAMESPACE, "SpanCount", "channel", "CHAT", {"stat": "Sum", "label": "CHAT Spans"}],
                [NAMESPACE, "InferenceCount", "channel", "VOICE", {"stat": "Sum", "label": "VOICE Inference"}],
                [NAMESPACE, "InferenceCount", "channel", "CHAT", {"stat": "Sum", "label": "CHAT Inference"}],
            ],
            "period": PERIOD,
            "region": region,
            "title": "Pipeline Throughput",
            "view": "timeSeries",
        },
    })
    y += 6

    return json.dumps({"widgets": widgets})
