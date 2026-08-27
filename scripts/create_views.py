#!/usr/bin/env python3
"""Create the curated Athena views over the spans table.

Nine views, each exposing channel and an observation count, none aggregating
across channels.
"""

import boto3
import time

REGION = "eu-west-2"
DATABASE = "connect_ai_token_efficiency"
WORKGROUP = "primary"

VIEWS = {
    # -------------------------------------------------------------------------
    "v_span_enriched": """
CREATE OR REPLACE VIEW v_span_enriched AS
SELECT
    span_id, parent_span_id, span_name, status, error_type,
    start_timestamp, end_timestamp, duration_ms,
    usage_input_tokens, usage_output_tokens, usage_total_tokens,
    cache_read_input_tokens, cache_write_input_tokens, has_cache_fields,
    reconciliation_state,
    time_to_first_token_ms,
    request_model, request_max_tokens, response_finish_reasons,
    system_instructions_chars,
    chars_text, chars_reasoning, chars_tool,
    tokens_reasoning, tokens_text, tokens_tool,
    ai_agent_id, ai_agent_name, ai_agent_version, ai_agent_type,
    ai_agent_orchestrator_use_case,
    prompt_id, prompt_version,
    assistant_id, session_id,
    contact_id, initial_contact_id, instance_id,
    channel, escalated, dt
FROM spans
""",
    # -------------------------------------------------------------------------
    "v_contact_rollup": """
CREATE OR REPLACE VIEW v_contact_rollup AS
SELECT
    initial_contact_id AS contact_id,
    channel,
    instance_id,
    MAX(ai_agent_name) AS primary_agent,
    COUNT(*) AS span_count,
    COUNT(CASE WHEN span_name = 'inference' THEN 1 END) AS inference_count,
    SUM(usage_total_tokens) AS total_tokens,
    SUM(usage_input_tokens) AS input_tokens,
    SUM(usage_output_tokens) AS output_tokens,
    SUM(cache_read_input_tokens) AS cache_read_tokens,
    SUM(cache_write_input_tokens) AS cache_write_tokens,
    SUM(duration_ms) FILTER (WHERE span_name = 'inference') AS model_time_ms,
    SUM(duration_ms) FILTER (WHERE span_name = 'execute_tool') AS tool_time_ms,
    SUM(tokens_reasoning) AS reasoning_tokens,
    MAX(CASE WHEN escalated = true THEN 1 ELSE 0 END) AS escalated,
    COUNT(CASE WHEN span_name = 'invoke_agent' THEN 1 END) AS turns,
    COUNT(CASE WHEN status = 'ERROR' AND error_type = 'barge_in' THEN 1 END) AS barge_in_count,
    MAX(dt) AS dt
FROM spans
WHERE initial_contact_id IS NOT NULL
GROUP BY initial_contact_id, channel, instance_id
""",
    # -------------------------------------------------------------------------
    "v_agent_daily": """
CREATE OR REPLACE VIEW v_agent_daily AS
SELECT
    ai_agent_name,
    ai_agent_version,
    channel,
    dt,
    COUNT(*) AS span_count,
    COUNT(CASE WHEN span_name = 'inference' AND usage_total_tokens IS NOT NULL THEN 1 END) AS inference_spans,
    SUM(usage_total_tokens) AS total_tokens,
    SUM(usage_output_tokens) AS output_tokens,
    SUM(tokens_reasoning) AS reasoning_tokens,
    COUNT(DISTINCT initial_contact_id) AS contacts,
    AVG(time_to_first_token_ms) AS avg_ttft_ms,
    APPROX_PERCENTILE(time_to_first_token_ms, 0.90) AS p90_ttft_ms,
    AVG(duration_ms) FILTER (WHERE span_name = 'inference') AS avg_inference_duration_ms,
    SUM(CASE WHEN status = 'ERROR' AND error_type = 'barge_in' THEN usage_total_tokens ELSE 0 END) AS barge_in_discarded_tokens,
    SUM(CASE WHEN has_cache_fields = true THEN 1 ELSE 0 END) AS spans_with_cache,
    SUM(CASE WHEN span_name = 'inference' THEN 1 ELSE 0 END) AS inference_total
FROM spans
WHERE ai_agent_name IS NOT NULL
GROUP BY ai_agent_name, ai_agent_version, channel, dt
""",
    # -------------------------------------------------------------------------
    "v_cache_state": """
CREATE OR REPLACE VIEW v_cache_state AS
SELECT
    ai_agent_name,
    ai_agent_version,
    channel,
    COUNT(*) AS inference_spans,
    SUM(CASE WHEN has_cache_fields = true THEN 1 ELSE 0 END) AS with_cache,
    CASE
        WHEN SUM(CASE WHEN has_cache_fields = true THEN 1 ELSE 0 END) = COUNT(*) THEN 'ON'
        WHEN SUM(CASE WHEN has_cache_fields = true THEN 1 ELSE 0 END) = 0 THEN 'OFF'
        ELSE 'MIXED'
    END AS cache_state,
    SUM(cache_read_input_tokens) AS cache_read_tokens,
    SUM(usage_input_tokens) AS fresh_input_tokens,
    CASE
        WHEN SUM(cache_read_input_tokens) + SUM(usage_input_tokens) > 0
        THEN CAST(SUM(cache_read_input_tokens) AS DOUBLE)
             / (SUM(cache_read_input_tokens) + SUM(usage_input_tokens))
        ELSE NULL
    END AS cache_hit_ratio,
    SUM(usage_total_tokens) AS total_tokens
FROM spans
WHERE span_name = 'inference' AND usage_total_tokens IS NOT NULL
GROUP BY ai_agent_name, ai_agent_version, channel
""",
    # -------------------------------------------------------------------------
    "v_reasoning_split": """
CREATE OR REPLACE VIEW v_reasoning_split AS
SELECT
    ai_agent_name,
    channel,
    COUNT(*) AS apportioned_spans,
    SUM(chars_text) AS chars_text,
    SUM(chars_reasoning) AS chars_reasoning,
    SUM(chars_tool) AS chars_tool,
    SUM(tokens_reasoning) AS tokens_reasoning,
    SUM(tokens_text) AS tokens_text,
    SUM(tokens_tool) AS tokens_tool,
    SUM(usage_output_tokens) AS output_tokens,
    CAST(SUM(chars_reasoning) AS DOUBLE) / NULLIF(SUM(chars_text) + SUM(chars_reasoning) + SUM(chars_tool), 0) AS share_incl_tool,
    CAST(SUM(chars_reasoning) AS DOUBLE) / NULLIF(SUM(chars_text) + SUM(chars_reasoning), 0) AS share_excl_tool,
    COUNT(CASE WHEN chars_reasoning = 0 OR chars_reasoning IS NULL THEN 1 END) AS zero_reasoning_spans
FROM spans
WHERE span_name = 'inference' AND chars_text IS NOT NULL
GROUP BY ai_agent_name, channel
""",
    # -------------------------------------------------------------------------
    "v_latency_percentiles": """
CREATE OR REPLACE VIEW v_latency_percentiles AS
SELECT
    ai_agent_name,
    request_model,
    channel,
    COUNT(*) AS n,
    APPROX_PERCENTILE(time_to_first_token_ms, 0.50) AS ttft_p50,
    APPROX_PERCENTILE(time_to_first_token_ms, 0.90) AS ttft_p90,
    APPROX_PERCENTILE(time_to_first_token_ms, 0.99) AS ttft_p99,
    AVG(duration_ms) AS avg_duration_ms,
    APPROX_PERCENTILE(duration_ms, 0.90) AS p90_duration_ms,
    AVG(CAST(usage_output_tokens AS DOUBLE)) AS avg_output_tokens,
    CASE WHEN AVG(CAST(usage_output_tokens AS DOUBLE)) > 0
         THEN AVG(CAST(duration_ms AS DOUBLE)) / AVG(CAST(usage_output_tokens AS DOUBLE))
         ELSE NULL
    END AS ms_per_output_token
FROM spans
WHERE span_name = 'inference' AND duration_ms IS NOT NULL
GROUP BY ai_agent_name, request_model, channel
""",
    # -------------------------------------------------------------------------
    "v_context_growth": """
CREATE OR REPLACE VIEW v_context_growth AS
SELECT
    ai_agent_name,
    channel,
    turn_ordinal,
    COUNT(*) AS n,
    AVG(usage_input_tokens) AS avg_input_tokens,
    APPROX_PERCENTILE(usage_input_tokens, 0.50) AS median_input_tokens
FROM (
    SELECT
        ai_agent_name, channel, usage_input_tokens,
        ROW_NUMBER() OVER (
            PARTITION BY initial_contact_id
            ORDER BY start_timestamp
        ) AS turn_ordinal
    FROM spans
    WHERE span_name = 'inference' AND usage_input_tokens IS NOT NULL
          AND initial_contact_id IS NOT NULL
)
GROUP BY ai_agent_name, channel, turn_ordinal
""",
    # -------------------------------------------------------------------------
    "v_model_capability": """
CREATE OR REPLACE VIEW v_model_capability AS
SELECT
    request_model,
    COUNT(*) AS inference_spans,
    SUM(usage_total_tokens) AS total_tokens,
    SUM(usage_output_tokens) AS output_tokens,
    AVG(duration_ms) AS avg_duration_ms,
    AVG(time_to_first_token_ms) AS avg_ttft_ms,
    SUM(CASE WHEN has_cache_fields = true THEN 1 ELSE 0 END) AS spans_with_cache,
    COUNT(DISTINCT ai_agent_name) AS agents_using
FROM spans
WHERE span_name = 'inference' AND request_model IS NOT NULL
GROUP BY request_model
""",
    # -------------------------------------------------------------------------
    "v_data_quality": """
CREATE OR REPLACE VIEW v_data_quality AS
SELECT
    dt,
    COUNT(*) AS total_spans,
    COUNT(CASE WHEN span_name = 'inference' AND usage_total_tokens IS NOT NULL THEN 1 END) AS token_bearing,
    COUNT(CASE WHEN reconciliation_state = 'MISMATCH' THEN 1 END) AS reconciliation_mismatches,
    COUNT(CASE WHEN reconciliation_state = 'SKIPPED_NON_NUMERIC' THEN 1 END) AS skipped_non_numeric,
    COUNT(CASE WHEN reconciliation_state = 'SKIPPED_ABSENT_TOTAL' THEN 1 END) AS skipped_absent_total,
    COUNT(CASE WHEN channel = 'UNRESOLVED' THEN 1 END) AS unresolved_channels,
    COUNT(CASE WHEN has_cache_fields IS NULL THEN 1 END) AS null_cache_indicator
FROM spans
GROUP BY dt
""",
}


def run_query(client, query: str, database: str = DATABASE) -> str:
    resp = client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": database},
        WorkGroup=WORKGROUP,
    )
    qid = resp["QueryExecutionId"]
    while True:
        status = client.get_query_execution(QueryExecutionId=qid)
        state = status["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if state != "SUCCEEDED":
        reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
        raise RuntimeError(f"Query {state}: {reason}\n  SQL: {query[:200]}")
    return qid


def main():
    client = boto3.client("athena", region_name=REGION)

    print(f"Creating {len(VIEWS)} views in {DATABASE}...")
    for name, sql in VIEWS.items():
        print(f"  {name}...", end=" ")
        try:
            run_query(client, sql)
            print("OK")
        except RuntimeError as e:
            print(f"FAILED: {e}")

    print("\nDone. Views are queryable at:")
    print(f"  Athena > Database: {DATABASE}")
    for name in VIEWS:
        print(f"    {name}")


if __name__ == "__main__":
    main()
