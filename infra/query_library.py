"""Query_Library — Level 0 CloudWatch Logs Insights queries.

These require ZERO infrastructure. They run directly against the assistant log
groups using CloudWatch Logs Insights. No Lambda, no S3, no Firehose.

Limitations vs Level 1/2:
  - No channel dimension (logs don't carry it; DescribeContact required)
  - No reasoning split (output_messages parsing not possible in Insights)
  - No cache warm-up profile (needs turn ordering within a contact)
  - Slower: queries scan raw logs rather than pre-parsed metrics
  - Limited to log retention window (14 or 30 days)

Usage:
  1. Open CloudWatch > Logs Insights
  2. Select one or more /aws/wisdom/* log groups
  3. Paste a query below
  4. Set time range

Note: `output` is a reserved word in Logs Insights. Use aliases like `out_tokens`.
The span field uses Java toString format — `parse` extracts values positionally.

Deduplication at Level 0:
  These queries read the raw CloudWatch log stream directly. Each
  TRANSCRIPT_AI_AGENT_TRACE event is written once by the Connect service, and
  the backfill script writes to Firehose/S3 — never back to CloudWatch Logs — so
  the log stream itself is not affected by backfill re-runs. Level 0 therefore
  does not accumulate duplicates the way the S3 Span_Store can.

  If you ever need to guard against an at-least-once redelivery from the service,
  parse `span_id` and aggregate with `count_distinct(span_id)` instead of
  `count(*)`, e.g.:
      | parse span "span_id=*, " as span_id
      | stats count_distinct(span_id) as unique_spans
  The Level 1/2 path (S3 + Athena) enforces span_id dedup in the v_span_enriched
  view, so Level 0 is the only surface where you would add this manually.
"""

# Each query is a dict with name, description, and the Insights query string.
# These are provisioned as saved queries via CDK (aws_logs.QueryDefinition).

LOGS_INSIGHTS_QUERIES: dict[str, dict[str, str]] = {
    "token_summary": {
        "name": "L0 - Token Summary",
        "description": (
            "Total token counts across all inference spans. Shows fresh input, "
            "output, cache read, cache write, and total. Run against any "
            "/aws/wisdom/* log group."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "usage_input_tokens=*, " as input_tok
| parse span "usage_output_tokens=*, " as out_tok
| parse span "usage_total_tokens=*, " as total_tok
| parse span "cache_read_input_tokens=*, " as cache_read
| parse span "cache_write_input_tokens=*, " as cache_write
| stats sum(input_tok) as fresh_input_tokens,
        sum(out_tok) as output_tokens,
        sum(total_tok) as total_tokens,
        sum(cache_read) as cache_read_tokens,
        sum(cache_write) as cache_write_tokens,
        count(*) as inference_spans
""",
    },
    "tokens_per_agent": {
        "name": "L0 - Tokens per Agent",
        "description": (
            "Token volume broken down by AI agent name and version. "
            "Identifies which agent burns the most tokens."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "ai_agent_name=*, " as agent_name
| parse span "ai_agent_version=*, " as agent_version
| parse span "usage_total_tokens=*, " as total_tok
| parse span "usage_input_tokens=*, " as input_tok
| parse span "usage_output_tokens=*, " as out_tok
| stats sum(total_tok) as total_tokens,
        sum(input_tok) as input_tokens,
        sum(out_tok) as output_tokens,
        count(*) as inference_calls,
        avg(total_tok) as avg_tokens_per_call
    by agent_name, agent_version
| sort total_tokens desc
""",
    },
    "ttft_percentiles": {
        "name": "L0 - TTFT Percentiles",
        "description": (
            "Time to first token at p50, p90, p99 per agent. "
            "This is how quickly the AI agent's response begins. "
            "Not available in the OOTB AI Agent Performance dashboard."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "ai_agent_name=*, " as agent_name
| parse span "time_to_first_token_ms=*, " as ttft
| stats pct(ttft, 50) as ttft_p50,
        pct(ttft, 90) as ttft_p90,
        pct(ttft, 99) as ttft_p99,
        avg(ttft) as ttft_avg,
        count(*) as n
    by agent_name
| sort ttft_p90 desc
""",
    },
    "cache_state_detection": {
        "name": "L0 - Cache State per Agent",
        "description": (
            "Detects whether caching is active per agent by checking if "
            "cache_read_input_tokens or cache_write_input_tokens fields are "
            "present in the span. A count of 0 means caching is OFF for that agent."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "ai_agent_name=*, " as agent_name
| parse span "cache_read_input_tokens=*, " as cache_read
| stats count(*) as total_spans,
        sum(ispresent(cache_read)) as spans_with_cache,
        sum(cache_read) as total_cache_read
    by agent_name
| display agent_name, total_spans, spans_with_cache,
          concat(toString(spans_with_cache * 100 / total_spans), "%") as cache_coverage
| sort total_spans desc
""",
    },
    "model_usage": {
        "name": "L0 - Model Attribution",
        "description": (
            "Token volume and latency by model. Shows which models are in use "
            "and their relative performance. Not available in the data lake "
            "until ai_prompt dataset is associated."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "request_model=*, " as model
| parse span "usage_total_tokens=*, " as total_tok
| parse span "usage_output_tokens=*, " as out_tok
| parse span "time_to_first_token_ms=*, " as ttft
| stats sum(total_tok) as total_tokens,
        sum(out_tok) as output_tokens,
        avg(ttft) as avg_ttft_ms,
        count(*) as inference_calls
    by model
| sort total_tokens desc
""",
    },
    "tokens_per_contact": {
        "name": "L0 - Tokens per Contact",
        "description": (
            "Token consumption grouped by contact. Shows the distribution — "
            "identify the expensive outliers. Sort by total to find the "
            "contacts driving cost."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "initial_contact_id=*, " as contact_id
| parse span "ai_agent_name=*, " as agent_name
| parse span "usage_total_tokens=*, " as total_tok
| parse span "time_to_first_token_ms=*, " as ttft
| stats sum(total_tok) as total_tokens,
        count(*) as inference_calls,
        max(ttft) as max_ttft_ms,
        avg(ttft) as avg_ttft_ms
    by contact_id, agent_name
| sort total_tokens desc
| limit 50
""",
    },
    "barge_in_waste": {
        "name": "L0 - Barge-In Token Waste",
        "description": (
            "Tokens discarded when a voice invocation is abandoned mid-generation "
            "(error_type=barge_in). These are tokens generated but not used. "
            "Typically a voice-channel event; the metric is reported per channel."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "status=*, " as span_status
| parse span "error_type=*, " as err_type
| parse span "usage_total_tokens=*, " as total_tok
| parse span "ai_agent_name=*, " as agent_name
| filter span_status = "ERROR" and err_type = "barge_in"
| stats sum(total_tok) as discarded_tokens,
        count(*) as barge_in_events
    by agent_name
""",
    },
    "output_ceiling": {
        "name": "L0 - Output Ceiling Proximity",
        "description": (
            "Spans where output tokens approach the max_tokens limit (>=90%). "
            "If output is being truncated, responses are cut off mid-sentence. "
            "request_max_tokens is not available in the data lake."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "usage_output_tokens=*, " as out_tok
| parse span "request_max_tokens=*, " as max_tok
| parse span "ai_agent_name=*, " as agent_name
| parse span "initial_contact_id=*, " as contact_id
| parse span "response_finish_reasons=*, " as finish_reasons
| filter out_tok >= max_tok * 0.9
| display @timestamp, agent_name, contact_id, out_tok, max_tok, finish_reasons
| sort @timestamp desc
""",
    },
    "instruction_size": {
        "name": "L0 - System Instruction Size per Agent",
        "description": (
            "Estimated token size of system instructions per agent. "
            "Uses calibrated coefficient: chars / 3.63 + 403 tokens overhead. "
            "Large instructions are repeated every turn — a major cost driver."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| filter span_name = "inference"
| parse span "ai_agent_name=*, " as agent_name
| parse span "system_instructions=*, input_messages" as sys_instr
| stats avg(strlen(sys_instr)) as avg_chars,
        max(strlen(sys_instr)) as max_chars,
        count(*) as n
    by agent_name
| display agent_name, avg_chars, max_chars, n,
          floor(avg_chars / 3.63 + 403) as est_tokens
| sort est_tokens desc
""",
    },
    "span_overview": {
        "name": "L0 - Span Type Overview",
        "description": (
            "Count of each span type (inference, invoke_agent, execute_tool, "
            "escalate_agent). Gives a quick sense of agent behaviour — how "
            "many tool calls per invocation, escalation rate, etc."
        ),
        "query": """\
filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*, " as span_name
| parse span "status=*, " as span_status
| stats count(*) as span_count
    by span_name, span_status
| sort span_count desc
""",
    },
}
