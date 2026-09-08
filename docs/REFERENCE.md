# Reference

Detailed reference material for the Amazon Connect Customer AI Agent Token Usage
Dashboard sample. For an overview, deployment steps, and prerequisites, see the
[README](../README.md).

## Contents

- [What is out of scope](#what-is-out-of-scope)
- [Falsified findings](#falsified-findings)
- [Required IAM permissions](#required-iam-permissions)
- [Calibrated constants](#calibrated-constants)
- [Connecting BI tools](#connecting-bi-tools)
- [Cost model](#cost-model)
- [Log retention constraint](#log-retention-constraint)
- [Optional upgrade: Connect analytics data lake](#optional-upgrade-connect-analytics-data-lake)
- [Validation dataset](#validation-dataset)
- [Evidence base](#evidence-base)
- [Repository structure](#repository-structure)

---

## What is out of scope

| Capability | Why excluded | Where to get it |
|---|---|---|
| Model-evaluated quality scores | LLM-evaluated, 24h refresh cycle | `ai_session.goal_success_rate`, `faithfulness_score`, `completeness_score` |
| Customer sentiment | Tested and falsified: r=+0.043, n=7, near-constant dependent variable | `contact_lens_conversational_analytics.sentiment_*` |
| Talk-time, silence, interruption measures | Contact Lens voice-only, disjoint from chat | `contact_lens_conversational_analytics.non_talk_time_total_ms` |
| Thumbs-up / thumbs-down feedback | Captured via a different event | `TRANSCRIPT_RESULT_FEEDBACK` events |
| Token cost in dollars | Connect bills per minute/message, not per token | Cost Explorer `ai-end-customer-mins`, `ai-chat-message` |

These capability classes are available through the Connect analytics data lake as
an optional upgrade path — see [below](#optional-upgrade-connect-analytics-data-lake).

## Falsified findings

Two candidate widgets were designed, tested, and deliberately excluded because the
evidence did not support them:

1. **Token consumption vs customer sentiment.** Correlation r=+0.043 at n=7, with
   sentiment taking only 4 distinct values (4 of 7 at zero). All correlations were
   weak and positive — the opposite of the hypothesis. The relationship does not
   hold in this data.

2. **Input tokens drive total invocation duration.** t=1.74 (not significant) on
   the 132-span corpus, but significant on the 106-span corpus, with the sign
   flipping across subgroups. The relationship is unresolved rather than refuted,
   and is excluded for measured sample dependence — a metric that reverses between
   samples should not appear on a dashboard.

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
| `athena:StartQueryExecution`, `GetQueryExecution` | `*` (Athena has no resource-level scoping for these actions) |
| `glue:GetDatabase`, `GetTable`, `GetPartitions`, `CreateTable`, `UpdateTable`, `DeleteTable` | The `connect_ai_token_efficiency` database |
| `s3:GetObject`, `PutObject`, `ListBucket`, `GetBucketLocation` | Span_Store bucket and the Athena results bucket |

### Deploying user or role

Standard AWS CDK deployment permissions (AWS CloudFormation, IAM, AWS Lambda,
Amazon S3, Amazon DynamoDB, Amazon Data Firehose, AWS Glue, Amazon Athena, Amazon
CloudWatch, and Amazon CloudWatch Logs).

---

## Calibrated constants

All values were measured against the validation dataset (n=132 token-bearing
spans). See the [Validation dataset](#validation-dataset) section for the caveats.

| Constant | Value | Provenance |
|---|---|---|
| Characters per input token | 3.63 | Regression of `usage_input_tokens + cache` on `len(system_instructions + input_messages)`, R²=0.971 |
| Prompt token intercept | 403 tokens | Intercept of the same regression (tool-definition overhead) |
| Characters per output token | 3.45 | `output_messages` characters / `usage_output_tokens` |
| ms per output token | 9.25 | Bootstrap CI [7.94, 11.60]; decode-time r=+0.870; validated seven ways |
| ms per 1,000 input tokens (TTFT) | 33.6 | Monotonic across 5 quintiles; excludes one 8,420 ms cold-start outlier |

Note: `len(text) / 4` underestimates token count by a median of 14.6%. Use
`len(text) / 3.63 + 403` instead.

Model caveat: 131 of 132 spans in the validation dataset used
`eu.anthropic.claude-haiku-4-5`. These are Haiku figures. Recompute per model as
your traffic diversifies.

---

## Connecting BI tools

The Athena curated views are the interface. Any SQL-capable tool can consume them.
See each service's pricing page for current pricing.

### Amazon Managed Grafana

The only option that queries both Amazon CloudWatch metrics and Amazon Athena in
one pane.

1. Create a Grafana workspace in the same Region.
2. Add the CloudWatch data source (configured through IAM).
3. Add the Athena data source, pointing at the `connect_ai_token_efficiency` database.
4. Build panels from the views.

### Amazon QuickSight

Suited to business users. Connects to Athena natively.

1. Create a dataset from the Athena data source.
2. Select the `connect_ai_token_efficiency` database and a view (for example, `v_agent_daily`).
3. Build analyses and dashboards.

QuickSight reads Athena, not CloudWatch metrics. Natural-language querying requires
a paid QuickSight tier.

### Third-party BI tools

Third-party business intelligence tools (for example, Tableau or Microsoft
Power BI) can connect to Athena over JDBC/ODBC:

- Endpoint: `athena.<region>.amazonaws.com`, port 443
- Database: `connect_ai_token_efficiency`
- Authentication: IAM credentials or SAML

---

## Cost model

Cost scales with your contact and span volume. The resources this sample
provisions, and what drives each one's cost, are:

| Resource | Cost driver |
|---|---|
| AWS Lambda | Per invocation, one invocation per log batch |
| Amazon DynamoDB | On-demand reads and writes for the channel cache, with TTL cleanup |
| Amazon S3 (Span_Store) | Storage for the span records |
| Amazon Data Firehose | Per-GB ingestion |
| CloudWatch custom metrics | Bounded by the five fixed dimension sets |
| CloudWatch dashboard | Per dashboard |
| Amazon Athena | Data scanned per query; date partitioning keeps scans small |
| AWS Glue Data Catalog | Catalog object count |

For an estimate against your own volume, use the
[AWS Pricing Calculator](https://calculator.aws/). Level 0 provisions no standing
infrastructure.

### What drives cost up

- **Dimension cardinality.** Five fixed dimension sets keep the metric count
  bounded. Adding dimensions increases the number of custom metrics.
- **Firehose record size.** Firehose is billed per record with a minimum billable
  record size, so many tiny records cost more than fewer larger ones. This sample
  packs records to approximately 1 MB to stay efficient. See the
  [Amazon Data Firehose pricing page](https://aws.amazon.com/firehose/pricing/)
  for the current increment.
- **Athena scan size.** Date partitioning and columnar views keep scans small.

---

## Log retention constraint

Assistant log group retention bounds how far Level 0 and Level 1 can look back. In
the validation account, two of three log groups retained 14 days and one retained
30 days.

Consequences:

- A 14-day alarm baseline on a 14-day log group has zero margin.
- Re-retrieving the corpus after the retention window returned 928 of the original
  1,205 events (23% lost).
- The Level 2 Span_Store is the only durable history, which is the strongest reason
  to adopt Level 2 over Level 1.

Raise log retention, or shorten the baseline window, so the baseline can fill.

---

## Optional upgrade: Connect analytics data lake

For the capability classes that logs cannot supply, associate the five AI datasets:

```bash
aws connect batch-associate-analytics-data-set \
  --instance-id <your-instance-id> \
  --data-set-ids ai_prompt ai_session ai_agent ai_tool ai_agent_knowledge_base \
  --target-account-id <your-account-id>
```

Note: this creates an AWS Lake Formation resource share.

| Dataset | What it adds |
|---|---|
| `ai_prompt` | `input_token`, `output_token`, `model_id` — raw tokens and model attribution that partially overlap this sample. Does not include cache tokens, TTFT, or the reasoning split. |
| `ai_session` | `goal_success_rate`, `faithfulness_score`, `completeness_score`, `is_handed_off` |
| `ai_tool` | `ai_tool_name`, accuracy scores |
| `ai_agent` | Invocation counts, helpfulness ratings |
| `ai_agent_knowledge_base` | Knowledge base reference tracking |

Join to `contact_lens_conversational_analytics` on `contact_id` for sentiment and
talk-time measures. The data lake is daily batch, so it does not replace the
near-real-time path in this sample.

---

## Validation dataset

Built and validated against a single Amazon Connect instance in `eu-west-2`,
across 3 assistant log groups, 1,205 events, 296 spans, 132 token-bearing
inference spans, and 28 contacts (23 Jul – 6 Aug 2026).

Key findings:

- Model inference time accounted for 74.8% of voice dead air, independently
  corroborated through Contact Lens `NonTalkTime` across 7 voice contacts.
- 9.25 ms per output token (bootstrap CI [7.94, 11.60]; approximately 108
  tokens/second).
- Reasoning was 43.7% of output characters (51.2% excluding tool payloads) on the
  original 132-span corpus. On the current retained corpus (106 spans): 41.7% and
  48.3%.
- The token identity held with 0 mismatches across 132 spans.
- `usage_input_tokens` is fresh input only — summing input and output undercounts
  by 4,301 tokens per turn when caching is active.
- The channel split was even (14 VOICE / 14 CHAT contacts).
- Chat escalation was 86% against a 32% blended figure — do not blend channels.
- Assistant log delivery lag: p50 5.9s, p90 10.4s, p99 11.4s, maximum 22.7s.

Sample-size caveat: 28 contacts, 132 token-bearing spans, 7 non-contiguous days,
3 of 6 agents at 5 or fewer calls, and 131 of 132 spans on a single model. This is
sufficient to validate the schema and method, and insufficient to set thresholds.

---

## Evidence base

All coefficients, field-availability facts, and negative results are recorded in
[`DESIGN.md`](../DESIGN.md) in the repository root. That document is the evidence
base and is preserved as-is.

---

## Repository structure

```
src/connect_ai_tokens/     Core library (Lambda handler and all components)
  span_parser.py           Depth-aware parser for the Java toString span format
  reconciliation.py        Token identity verification
  reasoning.py             Reasoning apportionment from output_messages
  channel.py               Channel resolution via DescribeContact
  metrics.py               EMF metric publisher (five bounded dimension sets)
  stats_gate.py            Statistical integrity guardrails
  insights.py              Regression detector, recommendations, comparison
  model_catalog.py         ListModels wrapper
  trace_viewer.py          Custom widget Lambda (voice and chat)
  handler.py               Lambda entry point
  config.py                Environment-driven configuration
  constants.py             Calibrated constants with provenance

infra/                     AWS CDK application
  app.py                   Entry point (account, Region, log groups)
  stacks/ingestion.py      All resources (Lambda, DynamoDB, Firehose, S3, Glue, and more)
  dashboard.py             CloudWatch dashboard definition
  queries.py               Athena named queries
  query_library.py         Level 0 Logs Insights queries
  views_cr.py              Custom resource for Athena view creation

scripts/
  backfill.py              Historical data loader (safe to re-run)

tests/                     147 tests (pytest and Hypothesis)
  fixtures/                Validated production spans
  test_span_parser.py      32 tests, including the round-trip property
  test_reconciliation.py   11 tests (0-mismatch assertion)
  test_reasoning.py        13 tests (conservation property)
  test_channel.py          16 tests (cache, retry, negative cache)
  test_metrics.py          15 tests (dimension sets, cache state)
  test_stats_gate.py       20 tests (suppression calibration)
  test_handler.py          14 tests (batch isolation, EMF output)
  test_e2e.py              26 tests (full-pipeline replay against the manifest)

docs/                      Architecture diagrams and reference
  architecture.drawio      Editable source (3 pages)
  architecture.png         Deployment-level architecture
  data-flow.png            Lambda processing flow
  signal-coverage.png      Coverage vs built-in metrics and the data lake
  REFERENCE.md             This document

DESIGN.md                  Evidence base (preserved as-is)
```
