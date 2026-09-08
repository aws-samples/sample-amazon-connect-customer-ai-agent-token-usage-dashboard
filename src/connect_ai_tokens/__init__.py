"""Token consumption observability for Amazon Connect Customer AI agents.

Reads ``TRANSCRIPT_AI_AGENT_TRACE`` events from Amazon Connect Customer AI agent
(Connect Assistant) CloudWatch log groups, or spans from the
``qconnect:ListSpans`` API, and turns them into token-usage metrics.

This is the correctness core — parser, reconciliation, reasoning apportionment,
channel resolution, statistical gate, metric publisher.
"""

__version__ = "1.0.0"
