"""Athena named queries for Level 2 analytics.

These query the connect_ai_token_efficiency.spans Glue table (populated by
Firehose at Level 2). Each answers a question the CloudWatch metrics cannot:
they need ordering, windowing, or joins across spans within a contact.

All queries are channel-scoped. Replace ${DATABASE} and ${TABLE} at deploy time.
"""

from __future__ import annotations

DATABASE = "connect_ai_token_efficiency"
TABLE = "spans"

QUERIES: dict[str, dict[str, str]] = {
    "context_growth_curve": {
        "description": (
            "Median input tokens by turn ordinal within a contact. "
            "Shows how the context window fills as the conversation progresses. "
            "A steepening curve signals the agent is accumulating history without summarisation."
        ),
        "sql": f"""
-- Context Growth Curve: median input tokens by turn ordinal
-- Steepening = history accumulation without summarisation
WITH ordered AS (
    SELECT
        contact_id,
        channel,
        ai_agent_name,
        usage_input_tokens,
        ROW_NUMBER() OVER (
            PARTITION BY contact_id
            ORDER BY start_timestamp
        ) AS turn_ordinal
    FROM "{DATABASE}"."{TABLE}"
    WHERE span_name = 'inference'
      AND usage_input_tokens IS NOT NULL
      AND dt >= date_format(date_add('day', -14, current_date), '%Y-%m-%d')
)
SELECT
    channel,
    ai_agent_name,
    turn_ordinal,
    approx_percentile(usage_input_tokens, 0.5) AS median_input_tokens,
    approx_percentile(usage_input_tokens, 0.9) AS p90_input_tokens,
    count(*) AS n
FROM ordered
WHERE turn_ordinal <= 20
GROUP BY channel, ai_agent_name, turn_ordinal
ORDER BY channel, ai_agent_name, turn_ordinal
""",
    },
    "cache_warmup_profile": {
        "description": (
            "Cache read vs write by turn ordinal. Turn 1 should show a cache_write "
            "(priming the system prompt), subsequent turns should show cache_read. "
            "If cache_write repeats after turn 1, the cache is being evicted mid-conversation."
        ),
        "sql": f"""
-- Cache Warm-up Profile: read vs write by turn ordinal
-- Turn 1 = cache_write (prime), Turn 2+ = cache_read (hit)
-- Repeated writes after turn 1 = cache eviction problem
WITH ordered AS (
    SELECT
        contact_id,
        channel,
        ai_agent_name,
        usage_input_tokens,
        COALESCE(cache_read_input_tokens, 0) AS cache_read,
        COALESCE(cache_write_input_tokens, 0) AS cache_write,
        has_cache_fields,
        ROW_NUMBER() OVER (
            PARTITION BY contact_id
            ORDER BY start_timestamp
        ) AS turn_ordinal
    FROM "{DATABASE}"."{TABLE}"
    WHERE span_name = 'inference'
      AND has_cache_fields = true
      AND dt >= date_format(date_add('day', -14, current_date), '%Y-%m-%d')
)
SELECT
    channel,
    ai_agent_name,
    turn_ordinal,
    avg(usage_input_tokens) AS avg_fresh_input,
    avg(cache_read) AS avg_cache_read,
    avg(cache_write) AS avg_cache_write,
    count(*) AS n
FROM ordered
WHERE turn_ordinal <= 12
GROUP BY channel, ai_agent_name, turn_ordinal
ORDER BY channel, ai_agent_name, turn_ordinal
""",
    },
    "agent_version_comparison": {
        "description": (
            "Per-agent-version token and latency comparison. Use to detect regressions "
            "when a new prompt version ships — tokens up + TTFT up = bloated prompt."
        ),
        "sql": f"""
-- Agent Version Comparison: detect regressions between prompt versions
-- Tokens up + TTFT up after a version change = bloated prompt
SELECT
    channel,
    ai_agent_name,
    ai_agent_version,
    prompt_version,
    count(*) AS inference_count,
    count(DISTINCT contact_id) AS contacts,
    avg(usage_input_tokens) AS avg_input_tokens,
    avg(usage_output_tokens) AS avg_output_tokens,
    avg(usage_input_tokens + usage_output_tokens
        + COALESCE(cache_read_input_tokens, 0)
        + COALESCE(cache_write_input_tokens, 0)) AS avg_total_tokens,
    approx_percentile(time_to_first_token_ms, 0.5) AS median_ttft_ms,
    approx_percentile(time_to_first_token_ms, 0.9) AS p90_ttft_ms,
    approx_percentile(duration_ms, 0.5) AS median_duration_ms,
    avg(system_instructions_chars) AS avg_prompt_chars,
    -- Cache state: fraction of spans with cache fields present
    CAST(sum(CASE WHEN has_cache_fields THEN 1 ELSE 0 END) AS DOUBLE)
        / count(*) AS cache_coverage
FROM "{DATABASE}"."{TABLE}"
WHERE span_name = 'inference'
  AND dt >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
GROUP BY channel, ai_agent_name, ai_agent_version, prompt_version
HAVING count(*) >= 5
ORDER BY ai_agent_name, ai_agent_version DESC, prompt_version DESC
""",
    },
    "reasoning_distribution": {
        "description": (
            "Per-span reasoning share distribution. The headline '49% is reasoning' "
            "hides that individual spans range 0-82%. This shows the full picture "
            "so you can identify agents with consistently high reasoning overhead."
        ),
        "sql": f"""
-- Reasoning Distribution: per-agent breakdown of reasoning overhead
-- Identifies agents where reasoning is consistently heavy vs occasional
SELECT
    channel,
    ai_agent_name,
    request_model,
    count(*) AS n,
    -- Headline shares
    CAST(sum(chars_reasoning) AS DOUBLE)
        / NULLIF(sum(chars_reasoning + chars_text + chars_tool), 0) AS reasoning_share_incl_tool,
    CAST(sum(chars_reasoning) AS DOUBLE)
        / NULLIF(sum(chars_reasoning + chars_text), 0) AS reasoning_share_excl_tool,
    -- Distribution
    approx_percentile(
        CAST(chars_reasoning AS DOUBLE) / NULLIF(chars_reasoning + chars_text + chars_tool, 0),
        0.5
    ) AS median_per_span_share,
    approx_percentile(
        CAST(chars_reasoning AS DOUBLE) / NULLIF(chars_reasoning + chars_text + chars_tool, 0),
        0.9
    ) AS p90_per_span_share,
    -- Impact: estimated seconds of dead air from reasoning
    sum(tokens_reasoning) * 9.25 / 1000.0 AS total_reasoning_seconds,
    sum(tokens_reasoning) * 9.25 / 1000.0
        / NULLIF(count(DISTINCT contact_id), 0) AS reasoning_seconds_per_contact,
    -- Zero-reasoning spans (some calls don't reason at all)
    sum(CASE WHEN chars_reasoning = 0 OR chars_reasoning IS NULL THEN 1 ELSE 0 END) AS zero_reasoning_spans
FROM "{DATABASE}"."{TABLE}"
WHERE span_name = 'inference'
  AND chars_reasoning IS NOT NULL
  AND dt >= date_format(date_add('day', -14, current_date), '%Y-%m-%d')
GROUP BY channel, ai_agent_name, request_model
HAVING count(*) >= 10
ORDER BY reasoning_share_incl_tool DESC
""",
    },
    "token_outlier_contacts": {
        "description": (
            "Top-N contacts by total token consumption. These are the tail that "
            "drives cost — the p90/max outliers. Join to contact records for "
            "business context (queue, disconnect reason, handle time)."
        ),
        "sql": f"""
-- Token Outlier Contacts: the expensive tail
-- Join these contact_ids to CTR for business context
SELECT
    contact_id,
    channel,
    ai_agent_name,
    count(*) AS inference_calls,
    sum(usage_input_tokens + usage_output_tokens
        + COALESCE(cache_read_input_tokens, 0)
        + COALESCE(cache_write_input_tokens, 0)) AS total_tokens,
    sum(usage_output_tokens) AS total_output_tokens,
    sum(duration_ms) AS total_model_time_ms,
    sum(COALESCE(tokens_reasoning, 0)) AS total_reasoning_tokens,
    max(time_to_first_token_ms) AS max_ttft_ms,
    CASE WHEN bool_or(has_cache_fields) THEN 'ON' ELSE 'OFF' END AS cache_state,
    CASE WHEN bool_or(error_type = 'barge_in') THEN true ELSE false END AS had_barge_in
FROM "{DATABASE}"."{TABLE}"
WHERE span_name = 'inference'
  AND dt >= date_format(date_add('day', -7, current_date), '%Y-%m-%d')
GROUP BY contact_id, channel, ai_agent_name
ORDER BY total_tokens DESC
LIMIT 50
""",
    },
    "daily_token_summary": {
        "description": (
            "Daily rollup of tokens, contacts, cache state and reasoning. "
            "Use as the primary trend view for week-over-week comparison."
        ),
        "sql": f"""
-- Daily Token Summary: primary trend view for WoW comparison
SELECT
    dt,
    channel,
    count(DISTINCT contact_id) AS contacts,
    count(*) AS inference_calls,
    sum(usage_input_tokens + usage_output_tokens
        + COALESCE(cache_read_input_tokens, 0)
        + COALESCE(cache_write_input_tokens, 0)) AS total_tokens,
    -- Per-contact average
    CAST(sum(usage_input_tokens + usage_output_tokens
        + COALESCE(cache_read_input_tokens, 0)
        + COALESCE(cache_write_input_tokens, 0)) AS DOUBLE)
        / NULLIF(count(DISTINCT contact_id), 0) AS tokens_per_contact,
    -- Cache
    CAST(sum(COALESCE(cache_read_input_tokens, 0)) AS DOUBLE)
        / NULLIF(sum(COALESCE(cache_read_input_tokens, 0) + usage_input_tokens), 0) AS cache_hit_ratio,
    -- Reasoning
    CAST(sum(COALESCE(chars_reasoning, 0)) AS DOUBLE)
        / NULLIF(sum(COALESCE(chars_reasoning, 0) + COALESCE(chars_text, 0) + COALESCE(chars_tool, 0)), 0) AS reasoning_share,
    -- TTFT
    approx_percentile(time_to_first_token_ms, 0.5) AS median_ttft_ms,
    approx_percentile(time_to_first_token_ms, 0.9) AS p90_ttft_ms,
    -- Duration
    sum(duration_ms) / 1000.0 AS total_model_time_seconds
FROM "{DATABASE}"."{TABLE}"
WHERE span_name = 'inference'
  AND dt >= date_format(date_add('day', -30, current_date), '%Y-%m-%d')
GROUP BY dt, channel
ORDER BY dt DESC, channel
""",
    },
}
