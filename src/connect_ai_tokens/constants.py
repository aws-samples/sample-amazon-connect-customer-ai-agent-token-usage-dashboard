"""Calibrated constants and their provenance.

Every value here was measured against real Connect Assistant logs. The docstring
on each is its provenance — do not change a value without re-running the
corresponding script in tests/fixtures/scripts/ and updating the provenance note.

Validation dataset: a single AWS account and Region, 3 assistant log groups,
1,205 events, 296 spans, 132 token-bearing inference spans, 28 contacts,
over a two-week window.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Tokenisation calibration
# --------------------------------------------------------------------------

CHARS_PER_INPUT_TOKEN = 3.63
"""Characters per input token.

Regressed usage_input_tokens + cache_read + cache_write on
len(system_instructions) + len(input_messages), n=132, R^2 = 0.971.
The naive len/4 underestimates by a median of 14.6%.
"""

PROMPT_TOKEN_INTERCEPT = 403
"""Fixed token overhead per inference call (tool definitions etc.).

Intercept of the same regression. Prompt-size estimates must add this, not just
divide characters.
"""

CHARS_PER_OUTPUT_TOKEN = 3.45
"""Characters per output token.

Derived from output_messages character totals against usage_output_tokens over
the same 132 spans (77,343 chars / 22,415 tokens). Distinct from the input
figure because output carries no tool-definition preamble.
"""

# --------------------------------------------------------------------------
# Latency coefficients
# --------------------------------------------------------------------------

MS_PER_OUTPUT_TOKEN = 9.25
"""Milliseconds of generation duration per output token.

Validated seven ways: structural decomposition (decode-time r = +0.870),
multiple regression controlling for input, partial correlation, within-contact
fixed effects, bootstrap CI, subgroup replication, significance (t = 11.78).
Implies ~108 tokens/sec, physically consistent with Claude Haiku 4.5.

MEASURED ON ONE MODEL. 131 of 132 spans were eu.anthropic.claude-haiku-4-5.
Comparison_Service must recompute this per model rather than reusing it.
"""

MS_PER_OUTPUT_TOKEN_CI = (7.94, 11.60)
"""Bootstrap 95% CI for MS_PER_OUTPUT_TOKEN, 2,000 resamples."""

MS_PER_1K_INPUT_TOKENS_TTFT = 33.6
"""Milliseconds of time-to-first-token per 1,000 input tokens.

Monotonic across all five input quintiles (574 -> 860 ms medians). Requires
excluding one 8,420 ms cold-start outlier, without which Pearson r is destroyed
(-0.025 with it, +0.329 without).

NOTE: input tokens do NOT predict total invocation duration (t = 1.74, bootstrap
CI crosses zero, sign flips across subgroups). This coefficient applies to TTFT
only. Never build a widget claiming input tokens drive total duration.
"""

# --------------------------------------------------------------------------
# Statistical gate defaults
# --------------------------------------------------------------------------

DEFAULT_MINIMUM_N = 30
DEFAULT_SIGNIFICANCE_LEVEL = 0.05
DEFAULT_BOOTSTRAP_RESAMPLES = 10_000
DEFAULT_MINIMUM_DISTINCT_VALUES = 10
DEFAULT_MAXIMUM_SINGLE_VALUE_SHARE = 0.50
DEFAULT_BASELINE_WINDOW_DAYS = 14

# --------------------------------------------------------------------------
# Span vocabulary
# --------------------------------------------------------------------------

SPAN_INFERENCE = "inference"
SPAN_INVOKE_AGENT = "invoke_agent"
SPAN_EXECUTE_TOOL = "execute_tool"
SPAN_ESCALATE_AGENT = "escalate_agent"
SPAN_BARGE_IN = "barge_in"

TOKEN_FIELDS = (
    "usage_input_tokens",
    "usage_output_tokens",
    "usage_total_tokens",
    "cache_read_input_tokens",
    "cache_write_input_tokens",
)
"""usage_input_tokens is FRESH input and excludes cache reads.

usage_total_tokens = usage_input_tokens + usage_output_tokens
                   + cache_read_input_tokens + cache_write_input_tokens
Verified with 0 mismatches across 132 spans. Summing input + output alone
undercounts wherever caching is active - by 4,301 tokens/turn on one agent.
"""

CACHE_FIELDS = ("cache_read_input_tokens", "cache_write_input_tokens")
"""ABSENT when caching is inactive, NOT zero. Test presence, never SUM."""

EVENT_AI_AGENT_TRACE = "TRANSCRIPT_AI_AGENT_TRACE"
EVENT_ORCHESTRATION_MESSAGE = "TRANSCRIPT_ORCHESTRATION_MESSAGE"
EVENT_LLM_INVOCATION = "TRANSCRIPT_LARGE_LANGUAGE_MODEL_INVOCATION"

# --------------------------------------------------------------------------
# Parser bounds (Requirement 2)
# --------------------------------------------------------------------------

MAX_SPAN_KEYS = 100
MAX_SPAN_CHARS = 262_144
MAX_BRACKET_DEPTH = 10

CHANNEL_UNRESOLVED = "UNRESOLVED"
DIMENSION_UNKNOWN = "UNKNOWN"
