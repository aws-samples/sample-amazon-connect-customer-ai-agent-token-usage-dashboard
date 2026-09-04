# Amazon Connect Customer AI Agent Token Usage Insights

> **Sample code for educational purposes only. Not for production use.**

[![License: MIT-0](https://img.shields.io/badge/License-MIT--0-yellow.svg)](https://opensource.org/licenses/MIT-0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![AWS CDK v2](https://img.shields.io/badge/AWS_CDK-v2-orange.svg)](https://docs.aws.amazon.com/cdk/v2/guide/home.html)
[![Tests](https://img.shields.io/badge/tests-147%20passed-brightgreen.svg)]()

Token, cache, reasoning, and time-to-first-token (TTFT) insights for Amazon
Connect AI agents (Amazon Q in Connect). This sample surfaces usage signals that
the built-in AI Agent Performance dashboard and the Connect analytics data lake
do not expose, and adds an insights layer that ranks them into evidence-backed
actions.

It reads from Amazon Connect Assistant event logs and `qconnect:ListSpans`. It
requires no Lake Formation resource share and no `BatchAssociateAnalyticsDataSet`
call.

---

## What this adds

These signals are not available in the OOTB AI Agent Performance dashboard or the
Connect analytics data lake. Each row cites the log field it derives from.

| Signal | Why it matters | Source |
|---|---|---|
| Prompt cache economics | No cache columns in the `ai_prompt` data lake table. 81% of tokens were uncached in the validation dataset. | Log span fields |
| Time to first token (TTFT) | The data lake carries total latency only. TTFT is the silence the caller hears before the agent responds. | `time_to_first_token_ms` |
| Reasoning token share | `output_token` is a single total. Roughly 44% of output was reasoning the customer never sees. | `output_messages` parsing |
| Barge-in token waste | The data lake carries a boolean `invocation_success`. This attributes the tokens discarded when a caller interrupts. | `status=ERROR, error_type=barge_in` |
| Output ceiling proximity | `request_max_tokens` is absent from the data lake. Detects truncation risk. | Span field comparison |
| Near-real-time alarms | The data lake is daily batch. These alarms fire within minutes. | EMF metrics |
| Context growth curve | Requires turn ordering within a contact. Not available as a metric. | Span ordering |
| Instruction size overhead | `system_instructions` appears only in the span, not the data lake. | Character count + calibrated coefficient |

See [`docs/signal-coverage.png`](docs/signal-coverage.png) for how these signals
relate to the OOTB metrics and the data lake.

## What this deliberately does NOT rebuild

The **31 built-in AI agent metrics** available through `connect:GetMetricDataV2`
and the AI Agent Performance dashboard:

**AI Agent:** `ACTIVE_AI_AGENTS`, `AI_AGENT_INVOCATIONS`,
`AI_AGENT_INVOCATION_SUCCESS`, `AI_AGENT_INVOCATION_SUCCESS_RATE`,
`AI_AGENT_RESPONSE_HELPFUL`, `AI_AGENT_RESPONSE_NOT_HELPFUL`,
`AVG_AI_AGENT_CONVERSATION_TURNS`

**AI Session:** `AI_HANDOFFS`, `AI_HANDOFF_RATE`, `AI_RESPONSE_COMPLETION_RATE`,
`AI_INVOLVED_CONTACTS`, `AVG_AI_CONVERSATION_TURNS`, `COMPLETENESS_SCORE`,
`FAITHFULNESS_SCORE`, `GOAL_SUCCESS_RATE`, `PROACTIVE_INTENTS_ANSWERED`,
`PROACTIVE_INTENTS_DETECTED`, `PROACTIVE_INTENTS_ENGAGED`,
`PROACTIVE_INTENT_ENGAGEMENT_RATE`, `PROACTIVE_INTENT_RESPONSE_RATE`

**AI Prompt:** `AI_PROMPT_INVOCATIONS`, `AI_PROMPT_INVOCATION_SUCCESS`,
`AI_PROMPT_INVOCATION_SUCCESS_RATE`, `AVG_AI_PROMPT_INVOCATION_LATENCY`

**AI Tool:** `AI_TOOL_INVOCATIONS`, `AI_TOOL_INVOCATION_SUCCESS`,
`AI_TOOL_INVOCATION_SUCCESS_RATE`, `AI_TOOL_PARAMETER_ACCURACY`,
`AI_TOOL_SELECTION_ACCURACY`, `AI_TOOL_UTILIZATION_ACCURACY`,
`AVG_AI_TOOL_INVOCATION_LATENCY`

**AI Knowledge Base:** `KNOWLEDGE_CONTENT_REFERENCES`

Use those from the OOTB AI Agent Performance dashboard or `GetMetricDataV2`
directly.

## What is out of scope

| Capability | Why excluded | Where to get it |
|---|---|---|
| Model-evaluated quality scores | LLM-evaluated, 24h refresh cycle | `ai_session.goal_success_rate`, `faithfulness_score`, `completeness_score` |
| Customer sentiment | Tested and falsified: r=+0.043, n=7, near-constant DV | `contact_lens_conversational_analytics.sentiment_*` |
| Talk-time, silence, interruption measures | Contact Lens voice-only, disjoint from chat | `contact_lens_conversational_analytics.non_talk_time_total_ms` |
| Thumbs-up / thumbs-down feedback | Captured via a different event | `TRANSCRIPT_RESULT_FEEDBACK` events |
| Token cost in dollars | Connect bills per minute/message, not per token | Cost Explorer `ai-end-customer-mins`, `ai-chat-message` |

These four capability classes are available via the Connect analytics data lake as
an optional upgrade path (see below).

## Falsified findings (excluded by evidence)

Two widgets were designed, tested, and deliberately excluded:

1. **Token consumption vs customer sentiment** — Correlation r=+0.043 at n=7 with
   sentiment taking only 4 distinct values (4 of 7 at zero). All correlations weak
   and positive (opposite to hypothesis). The relationship does not exist in this
   data.

2. **Input tokens drive total invocation duration** — t=1.74 (not significant) on
   the 132-span corpus, but t significant on the 106-span corpus. Sign flips
   across subgroups. Status: unresolved, not refuted. Excluded for measured sample
   dependence — a metric that reverses between samples should not be on a dashboard.

---

## Architecture

![Architecture](docs/architecture.png)

Data sources (Connect Assistant logs, `qconnect:ListSpans`,
`connect:DescribeContact`, `qconnect:ListModels`) feed three additive deployment
levels. Level 0 runs saved Logs Insights queries with no infrastructure. Level 1
adds a subscription filter, a Lambda parser, EMF metrics, a dashboard, and
alarms. Level 2 adds a Firehose to an S3 Span_Store, a Glue catalog, and Athena
curated views. Grafana, QuickSight, Tableau/Power BI, and the AWS consoles
consume the outputs.

The diagram source is [`docs/architecture.drawio`](docs/architecture.drawio)
(open with the Draw.io editor). Two companion views:

- [`docs/data-flow.png`](docs/data-flow.png) — the Lambda processing flow per invocation
- [`docs/signal-coverage.png`](docs/signal-coverage.png) — what this sample adds vs what Connect already provides

### Deployment levels (additive)

| Level | What you get | Infrastructure cost |
|---|---|---|
| **LEVEL_0** | 10 saved Logs Insights queries. Zero compute, zero storage. | $0 (queries cost ~$0.005 per GB scanned) |
| **LEVEL_1** | + Lambda parser, EMF metrics, CloudWatch dashboard, 3 alarms | ~$7-10/month |
| **LEVEL_2** | + S3 Span_Store, Firehose, Glue, Athena views, named queries | ~$10-15/month |

---

## Prerequisites

1. **AWS credentials** for the target account with permissions listed below.

2. **Connect AI agent logging enabled** on each assistant. This requires the
   `wisdom:AllowVendedLogDeliveryForResource` permission, then the CloudWatch
   log-delivery APIs (run in order, per assistant):
   ```bash
   aws logs put-delivery-source \
     --name "wisdom-<assistant-name>" \
     --resource-arn "arn:aws:wisdom:<region>:<account>:assistant/<id>" \
     --log-type EVENT_LOGS

   aws logs put-delivery-destination \
     --name "wisdom-<assistant-name>-dest" \
     --output-format json \
     --delivery-destination-configuration \
       destinationResourceArn="arn:aws:logs:<region>:<account>:log-group:/aws/wisdom/<assistant-name>"

   aws logs create-delivery \
     --delivery-source-name "wisdom-<assistant-name>" \
     --delivery-destination-arn "<destination-arn>"
   ```
   See [Enable logging for AI agents](https://docs.aws.amazon.com/connect/latest/adminguide/monitor-ai-agents.html).

3. **AI agent traces enabled** (for drill-down): call recording on, plus *Enable
   Bot Analytics, Transcripts, and AI Agent Traces* and *Enable Automated
   Interaction Logs* in the instance settings. AI agent traces are voice-channel
   only; `qconnect:ListSpans` covers both voice and chat.

4. **Python 3.11+** and **AWS CDK v2**:
   ```bash
   npm install -g aws-cdk
   pip install -e ".[infra,dev]"
   ```

5. **CDK bootstrap** (one-time per account/region):
   ```bash
   cdk bootstrap aws://<ACCOUNT_ID>/<REGION>
   ```

---

## Deploy

```bash
cd infra
cdk deploy
```

Single command. Provisions all resources for the configured deployment level.

### Configuration

No identifiers are hardcoded. Configure via CDK context — either edit the
`context` block in `infra/cdk.json`:

```json
{
  "app": "python3 app.py",
  "context": {
    "assistant_log_groups": "/aws/wisdom/your-assistant-1,/aws/wisdom/your-assistant-2",
    "connect_instance_ids": "your-instance-id-1,your-instance-id-2"
  }
}
```

Or pass them on the command line:

```bash
cdk deploy \
  -c assistant_log_groups="/aws/wisdom/your-assistant-1,/aws/wisdom/your-assistant-2" \
  -c connect_instance_ids="your-instance-id-1,your-instance-id-2"
```

Account and region default to your AWS credentials (`CDK_DEFAULT_ACCOUNT` /
`CDK_DEFAULT_REGION`). Override with `-c account=...` and `-c region=...` if needed.

### Backfill historical data

The subscription filter captures new events only. To load history (within log
retention), set the environment variables from your stack outputs, then run:

```bash
# Get the values from the deployed stack
aws cloudformation describe-stacks --stack-name ConnectAITokenEfficiency \
  --query 'Stacks[0].Outputs' --output table

export AWS_REGION="your-region"
export ASSISTANT_LOG_GROUPS="/aws/wisdom/your-assistant-1,/aws/wisdom/your-assistant-2"
export CONNECT_INSTANCE_IDS="your-instance-id-1,your-instance-id-2"
export DELIVERY_STREAM_NAME="<DeliveryStream output>"
export CHANNEL_CACHE_TABLE="<ChannelCacheTable output>"

python scripts/backfill.py
```

**Re-running backfill is safe.** Firehose delivery is at-least-once and the
script does not check what already landed in S3, so re-running writes the same
spans again. Every Athena curated view deduplicates by `span_id` (see the
`v_span_enriched` base view — all other views read from it), so no query,
dashboard, or metric ever reflects a duplicate regardless of how many times
backfill runs. If you also want the raw S3 objects to stay unique, empty the
`spans/` prefix before re-running.

### Teardown

```bash
cd infra && cdk destroy
```

Removes all resources. S3 bucket is configured with `autoDeleteObjects`.

---

## Required IAM permissions

### Lambda execution role (provisioned automatically)

| Action | Scope |
|---|---|
| `connect:DescribeContact` | Configured instance ARNs only (`instance/*/contact/*`) |
| `firehose:PutRecordBatch` | The provisioned delivery stream |
| `dynamodb:GetItem`, `PutItem` | The channel cache table |
| `s3:PutObject` | The Span_Store bucket |

### Views Creator Lambda (provisioned automatically)

| Action | Scope |
|---|---|
| `athena:StartQueryExecution`, `GetQueryExecution` | `*` (Athena has no resource-level scoping) |
| `glue:GetDatabase`, `GetTable`, `GetPartitions`, `CreateTable`, `UpdateTable`, `DeleteTable` | The `connect_ai_token_efficiency` database |
| `s3:GetObject`, `PutObject`, `ListBucket`, `GetBucketLocation` | Span_Store bucket + Athena results bucket |

### Deploying user/role

Standard CDK deployment permissions (CloudFormation, IAM, Lambda, S3, DynamoDB,
Firehose, Glue, Athena, CloudWatch, Logs).

---

## What you get after deploy

### CloudWatch Dashboard: `ConnectAI-TokenEfficiency`

13 widgets across 4 sections:
- **Token Economics:** Tokens/contact, TTFT p50/p90 with 3s threshold, barge-in waste
- **Cache Economics:** Hit ratio, token composition (read/write/fresh), total vs cached
- **Reasoning Efficiency:** Share (incl/excl tool), absolute tokens, output ceiling hits
- **Operational Health:** Reconciliation mismatches, decode speed monitor, pipeline throughput

### 3 CloudWatch Alarms

| Alarm | Trigger | Threshold |
|---|---|---|
| `ConnectAI-ContextLeak-TokensPerContact` | Tokens/contact exceeds baseline | 50,000 (placeholder) |
| `ConnectAI-TTFT-P90-DeadAir` | TTFT p90 exceeds caller patience | 3,000 ms |
| `ConnectAI-CacheRegression` | Cache hit ratio drops | 30% |

Replace thresholds with your baseline after 14 days of data.

### 10 Logs Insights Saved Queries (Level 0)

Available in CloudWatch > Logs Insights > Saved queries:
- `L0 - Token Summary`
- `L0 - Tokens per Agent`
- `L0 - TTFT Percentiles`
- `L0 - Cache State per Agent`
- `L0 - Model Attribution`
- `L0 - Tokens per Contact`
- `L0 - Barge-In Token Waste`
- `L0 - Output Ceiling Proximity`
- `L0 - System Instruction Size per Agent`
- `L0 - Span Type Overview`

### 9 Athena Curated Views

Database: `connect_ai_token_efficiency`

| View | Purpose |
|---|---|
| `v_span_enriched` | Deduplicated base view (one row per `span_id`) — all other views read from it |
| `v_contact_rollup` | Per-contact totals (tokens, model time, tool time, reasoning, escalation) |
| `v_agent_daily` | Per-agent per-day summary |
| `v_cache_state` | ON/OFF/MIXED per agent with hit ratio |
| `v_reasoning_split` | Reasoning share per agent (both denominators) |
| `v_latency_percentiles` | TTFT and duration per agent per model |
| `v_context_growth` | Input tokens by turn ordinal |
| `v_model_capability` | Per-model aggregates |
| `v_data_quality` | Reconciliation and coverage health |

### 6 Athena Named Queries

- `connect-ai-context_growth_curve`
- `connect-ai-cache_warmup_profile`
- `connect-ai-agent_version_comparison`
- `connect-ai-reasoning_distribution`
- `connect-ai-token_outlier_contacts`
- `connect-ai-daily_token_summary`

---

## Calibrated constants

All measured against the validation dataset (n=132 token-bearing spans).

| Constant | Value | Provenance |
|---|---|---|
| Characters per input token | 3.63 | Regression on `usage_input_tokens + cache` vs `len(system_instructions + input_messages)`, R²=0.971 |
| Prompt token intercept | 403 tokens | Same regression intercept (tool-definition overhead) |
| Characters per output token | 3.45 | `output_messages` chars / `usage_output_tokens` |
| ms per output token | 9.25 | Bootstrap CI [7.94, 11.60]. Decode-time r=+0.870. Validated 7 ways. |
| ms per 1,000 input tokens (TTFT) | 33.6 | Monotonic across 5 quintiles. Excludes one 8,420ms cold-start outlier. |

**Important:** `len(text) / 4` underestimates token count by a median of 14.6%.
Use `len(text) / 3.63 + 403` instead.

**Model caveat:** 131 of 132 spans are `eu.anthropic.claude-haiku-4-5`. These
are Haiku numbers. Recompute per model as traffic diversifies.

---

## Connecting BI tools

The curated views are the interface. Any SQL-capable tool can consume them. See
current pricing on each service's pricing page.

### Amazon Managed Grafana

The only option that queries both CloudWatch metrics and Athena in one pane.

1. Create a Grafana workspace in the same Region.
2. Add the CloudWatch data source (configured via IAM).
3. Add the Athena data source, pointing at the `connect_ai_token_efficiency` database.
4. Build panels from the views.

### Amazon QuickSight

Suited to business users. Connects to Athena natively.

1. Create a dataset from the Athena data source.
2. Select the `connect_ai_token_efficiency` database and a view (for example, `v_agent_daily`).
3. Build analyses and dashboards.

QuickSight reads Athena, not CloudWatch metrics. Natural-language querying
requires a paid QuickSight tier.

### Tableau / Power BI

Both connect to Athena over JDBC/ODBC:

- Endpoint: `athena.<region>.amazonaws.com`, port 443
- Database: `connect_ai_token_efficiency`
- Authentication: IAM credentials or SAML

---

## Cost model

### Verified infrastructure costs (at ~10,000 contacts/month, ~47,000 spans)

| Resource | Monthly cost | Notes |
|---|---|---|
| Lambda | ~$1-2 | 512MB, ~5s avg, 47k invocations |
| DynamoDB | <$1 | On-demand, ~10k items, TTL cleanup |
| S3 Span_Store | <$1 | ~100-200MB/month Parquet-ready JSON |
| Firehose | ~$1 | Per-GB ingestion |
| CloudWatch custom metrics | ~$3-5 | 5 dimension sets × ~13 metrics |
| CloudWatch dashboard | $3 | Fixed per dashboard |
| Athena queries (on-demand) | <$1 | $5/TB scanned, data is ~MB |
| Glue Data Catalog | $0 | Free for first million objects |
| **Total Level 2** | **~$10-15/month** | |
| **Total Level 1** (no S3/Firehose/Glue) | **~$7-10/month** | |
| **Total Level 0** | **$0** | Queries cost ~$0.005/GB scanned |

### What drives cost up

- **Dimension cardinality:** 5 fixed dimension sets keeps it bounded. Adding more
  dimensions multiplies metric cost.
- **Firehose minimum billing:** 5KB per record. Packing to ~1MB records (as
  implemented) avoids the 2.5x penalty.
- **Athena scan size:** Date partitioning + columnar views keeps scans small.

---

## Log retention constraint

Assistant log group retention bounds how far Level 0 and Level 1 can look back.
In the validation account, two of three log groups retained 14 days and one
retained 30 days.

**Consequences:**
- A 14-day alarm baseline on a 14-day log group has zero margin.
- Re-retrieving the corpus after the retention window returned 928 of the
  original 1,205 events (23% lost).
- The Level 2 Span_Store is the only durable history, which is the strongest
  reason to adopt Level 2 over Level 1.

Raise log retention, or shorten the baseline window, so the baseline can fill.

---

## Optional upgrade: Connect analytics data lake

For the four capability classes logs cannot supply, associate the 5 AI datasets:

```bash
aws connect batch-associate-analytics-data-set \
  --instance-id <your-instance-id> \
  --data-set-ids ai_prompt ai_session ai_agent ai_tool ai_agent_knowledge_base \
  --target-account-id <your-account-id>
```

**Warning:** This creates a Lake Formation resource share.

| Dataset | What it adds |
|---|---|
| `ai_prompt` | `input_token`, `output_token`, `model_id` — raw tokens and model attribution that partially overlap this sample. Lacks cache tokens, TTFT, and the reasoning split. |
| `ai_session` | `goal_success_rate`, `faithfulness_score`, `completeness_score`, `is_handed_off` |
| `ai_tool` | `ai_tool_name`, accuracy scores |
| `ai_agent` | Invocation counts, helpfulness ratings |
| `ai_agent_knowledge_base` | Knowledge base reference tracking |

Join to `contact_lens_conversational_analytics` on `contact_id` for sentiment and
talk-time measures. The data lake is daily batch, so it does not replace the
near-real-time path in this sample.

---

## Validation dataset

Built and validated against: account `101506645078`, region `eu-west-2`,
3 assistant log groups, 1,205 events, 296 spans, 132 token-bearing inference
spans, 28 contacts, 23 Jul - 6 Aug 2026.

**Key findings:**
- 74.8% of voice dead air is model inference time (independently corroborated
  via Contact Lens `NonTalkTime` across 7 voice contacts)
- 9.25 ms per output token (bootstrap CI [7.94, 11.60], 108 tokens/sec)
- 43.7% of output characters are reasoning (51.2% excluding tool payloads) on
  the original 132-span corpus. Current retained corpus (106 spans): 41.7% / 48.3%
- Token identity verified with 0 mismatches across 132 spans
- `usage_input_tokens` is FRESH input only — summing input + output undercounts
  by 4,301 tokens/turn when caching is active
- Channel split is 50/50 VOICE/CHAT (14/14 contacts)
- Chat escalation rate is 86% vs 32% blended — never blend channels
- Assistant log delivery lag: p50 5.9s, p90 10.4s, p99 11.4s, max 22.7s

**Sample-size caveat:** 28 contacts, 132 token-bearing spans, 7 non-contiguous
days, 3 of 6 agents at 5 or fewer calls, 131 of 132 spans on a single model.
Sufficient to validate schema and method. Insufficient to set thresholds.

---

## Evidence base

All coefficients, field-availability facts, and negative results are recorded in
`DESIGN.md` in the repository root. That document is the evidence base and is
preserved as-is.

---

## Repository structure

```
src/connect_ai_tokens/     Core library (Lambda handler + all components)
  span_parser.py           Depth-aware parser for Java toString span format
  reconciliation.py        Token identity verification
  reasoning.py             Reasoning apportionment from output_messages
  channel.py               Channel resolution via DescribeContact
  metrics.py               EMF metric publisher (5 bounded dimension sets)
  stats_gate.py            Statistical integrity guardrails
  insights.py              Regression detector, recommendations, comparison
  model_catalog.py         ListModels wrapper
  trace_viewer.py          Custom widget Lambda (voice + chat)
  handler.py               Lambda entry point
  config.py                Environment-driven configuration
  constants.py             Calibrated constants with provenance

infra/                     CDK application
  app.py                   Entry point (account, region, log groups)
  stacks/ingestion.py      All resources (Lambda, DDB, Firehose, S3, Glue, etc.)
  dashboard.py             CloudWatch dashboard JSON
  queries.py               Athena named queries
  query_library.py         Level 0 Logs Insights queries
  views_cr.py              Custom resource for Athena view creation

scripts/
  backfill.py              Historical data loader (idempotent-safe)

tests/                     147 tests (pytest + hypothesis)
  fixtures/                Validated production spans
  test_span_parser.py      32 tests including round-trip property
  test_reconciliation.py   11 tests (0-mismatch assertion)
  test_reasoning.py        13 tests (conservation property)
  test_channel.py          16 tests (cache, retry, negative cache)
  test_metrics.py          15 tests (dimension sets, cache state)
  test_stats_gate.py       20 tests (suppression calibration)
  test_handler.py          14 tests (batch isolation, EMF output)
  test_e2e.py              26 tests (full-pipeline replay against the manifest)

docs/                      Architecture diagrams
  architecture.drawio      Editable source (3 pages)
  architecture.png         Deployment-level architecture
  data-flow.png            Lambda processing flow
  signal-coverage.png      Coverage vs OOTB and the data lake

DESIGN.md                  Evidence base (preserved as-is)
```


---

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more
information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE)
file.
