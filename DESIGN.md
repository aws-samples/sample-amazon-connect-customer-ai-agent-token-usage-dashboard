# AI Agent Token Consumption Dashboard — Build Spec

Amazon Connect AI agents (Amazon Q in Connect). **Scope is deliberately narrow:
the token, cache, and model-attribution layer that Connect does not provide
today.** Everything already covered by the built-in AI Agent Performance
dashboard is explicitly out of scope.

**Validated against** account `101506645078`, region `eu-west-2`: 3 assistant log
groups, 1,205 events, 296 trace spans, 132 token-bearing inference spans,
28 contacts, 23 Jul – 6 Aug 2026. OOTB metrics tested live via
`GetMetricDataV2`.

---

## 1. What already exists — do not rebuild

Connect ships **31 AI agent metrics** via `GetMetricDataV2` and an **AI Agent
Performance dashboard** at *Analytics and optimization > Analytics dashboards >
AI Agent Performance*. Confirmed live in this account.

### 1a. Verified live response

Instance `cf6475f7…` (Bet365), 5–6 Aug, filtered to two AI agent IDs:

| OOTB metric | Value | My log-derived equivalent |
|---|---|---|
| `AI_AGENT_INVOCATIONS` | 13 | `invoke_agent` spans = 13 — **exact match** |
| `AI_PROMPT_INVOCATIONS` | 18 | `inference` spans = 18 — **exact match** |
| `AI_TOOL_INVOCATIONS` | 8 | `execute_tool` spans = 8 — **exact match** |
| `AI_HANDOFF_RATE` | 100% | escalation rate |
| `AVG_AI_AGENT_CONVERSATION_TURNS` | 2.46 | turns per contact |
| `AVG_AI_PROMPT_INVOCATION_LATENCY` | 3,804ms | inference span duration |
| `AVG_AI_TOOL_INVOCATION_LATENCY` | 1,029ms | `execute_tool` duration |

Span-derived counts reproduce OOTB metrics exactly. Any widget that recomputes
these from logs is pure duplication.

`GOAL_SUCCESS_RATE`, `FAITHFULNESS_SCORE`, `COMPLETENESS_SCORE` and
`AI_TOOL_UTILIZATION_ACCURACY` returned `null` — these are LLM-evaluated, refresh
every 24h, and require Connect Customer AI.

### 1b. The 31 OOTB metrics

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

Valid groupings (tested): `AI_AGENT_ID`, `AI_AGENT_TYPE`, `AI_USE_CASE`,
`AI_PROMPT_ID`. Filter key: `AI_AGENT_ID`.
Rejected: `AI_MODEL_ID`, `MODEL_ID`, `AI_AGENT_VERSION`, `AI_PROMPT_VERSION`,
`AI_TOOL_TYPE`, `AI_TOOL_NAME`.

### 1c. Per-contact drill-down also exists

**AI agent traces on the Contact details page** (Automated Interaction tab,
*Show flow & trace details*) already provide, per contact:

- Full span tree — invocation, prompt/inference, tool calls
- Latency per span
- **ESC** label where the agent escalated
- **ERR** label with description, including **AI barge in** and **Timeout**
- Tool metadata and input parameters
- Knowledge base citation references
- Model reasoning
- Links through to AI agent and prompt config pages

Requires: call recording enabled, *Enable Bot Analytics, Transcripts, and AI
Agent Traces*, and *Enable Automated Interaction Logs*. If Bot Analytics was
enabled before 5 Jun 2026 it must be disabled and re-enabled. Voice channel only.
Available ~30 min after contact ends.

### 1d. Widgets cut from the earlier draft as duplicates

| Cut | Superseded by |
|---|---|
| Containment rate | `AI_HANDOFF_RATE` — and `GOAL_SUCCESS_RATE` is stronger (LLM-evaluated resolution) |
| Escalation driver ranking | `AI_HANDOFF_RATE` + `AI_TOOL_SELECTION_ACCURACY` + `GOAL_SUCCESS_RATE`. My version reconstructed a "reason" that isn't a field; the OOTB accuracy scores answer the same question properly |
| Turns to resolution | `AVG_AI_AGENT_CONVERSATION_TURNS`, `AVG_AI_CONVERSATION_TURNS` |
| Customer turn count | `AVG_AI_CONVERSATION_TURNS` |
| Orchestration loop depth | `AVG_AI_AGENT_CONVERSATION_TURNS` |
| Tool reliability | `AI_TOOL_INVOCATION_SUCCESS_RATE`, `AVG_AI_TOOL_INVOCATION_LATENCY`, plus 3 accuracy scores |
| Volume & scale | `AI_INVOLVED_CONTACTS`, `ACTIVE_AI_AGENTS` |
| Doom-loop drill-down | AI agent traces on Contact details (ESC/ERR labels, per-span latency) |
| General inference latency | `AVG_AI_PROMPT_INVOCATION_LATENCY` |
| Version scorecard (non-token parts) | AI agents performance table drills to agent-version level |

---

## 1e. Tokens and model_id ARE in the analytics data lake

The Connect analytics data lake exposes **5 AI datasets** (confirmed available in
this account via `ListAnalyticsDataLakeDataSets`, 48 datasets total):
`ai_agent`, `ai_prompt`, `ai_session`, `ai_tool`, `ai_agent_knowledge_base`.

**`ai_prompt` carries token counts and model attribution.** Its own description:
tracks prompt invocation events including model usage, token counts, latency and
invocation success.

| Column | Type | Closes gap |
|---|---|---|
| `input_token` | bigint | **token consumption** |
| `output_token` | bigint | **token consumption** |
| `model_id` | string | **model attribution + comparison** |
| `invocation_latency_ms` | float | latency |
| `ai_prompt_version`, `ai_agent_version` | string | version scorecard |
| `ai_prompt_name`, `ai_agent_name`, `ai_agent_type` | string | attribution |
| `contact_id`, `ai_session_id`, `instance_id` | string | joins + turn ordinal |
| `invocation_success` | boolean | reliability |

`ai_session` additionally carries `is_handed_off`, `goal_success_rate`,
`faithfulness_score`, `completeness_score`, `avg_conversation_turns_in_response`.
`ai_tool` carries `ai_tool_name`, `ai_tool_type` and the three accuracy scores —
so **tool name is a first-class column here**, no nested JSON parsing needed.

Joins: `instance_id` to everything, `contact_id` to Contact Record / Contact Lens,
`ai_session_id` across all AI tables. Daily partition on `creation_timestamp`.

**This is the correct primary source for token and model analytics.** It is
cross-instance by nature, SQL-queryable via Athena, and joins to contact records
and Contact Lens for real business context.

Not currently enabled here: `ListAnalyticsDataAssociations` shows only
`contact_lens_conversational_analytics` associated, on one instance
(`1de8296e…`). The five AI datasets must be associated first.

### Gaps the data lake does NOT close

| Gap | Why it survives |
|---|---|
| **Cache tokens** | No `cache_read_token` / `cache_write_token` column. Verified: `total = input + output + cache_read + cache_write`, so `input_token + output_token` **undercounts wherever caching is active** — by 4,301 tokens per turn on Bet365 |
| **TTFT** | `invocation_latency_ms` is total invocation latency. `time_to_first_token_ms` is a separate span field. TTFT is the silence the caller hears |
| **System prompt size** | `system_instructions` exists only in the trace span |
| **Real-time** | Data lake is daily-partitioned batch, and `data_lake_last_processed_timestamp` is explicitly documented as unusable for freshness. Logs are near-real-time |
| **Barge-in attribution** | `invocation_success` is a boolean. `error_type=barge_in` distinguishes discarded work from genuine failure |
| **`request_max_tokens` / `response_finish_reasons`** | Not in the table — no output-ceiling or finish-reason analysis |

## 1e-bis. Audit corrections (verified this round)

### C1 — Channel split is 50/50 VOICE/CHAT

Resolved all 28 contacts via `connect describe-contact`:

| Channel | Contacts | Tokens |
|---|---|---|
| VOICE | 14 (50.0%) | 490,258 (54.5%) |
| CHAT | 14 (50.0%) | 409,184 (45.5%) |

Consequences:

- **Dead air / billable-minutes framing applies to voice only** — half the traffic.
  Chat bills per message.
- `non_talk_time_total_ms`, `talk_time_*_ms`, `interruptions_*` in Contact Lens are
  **voice-only**. For chat use `response_time_average_*_ms` instead.
- **AI agent traces are documented as voice-channel only**, so the Contact-details
  drill-down is unavailable for 50% of these contacts. ListSpans still works for both.
- Barge-in is a voice concept; both observed events were on the voice instance.

Every widget must carry a channel dimension. A blended voice+chat average is
meaningless.

### C2 — `WisdomInfo` removes the SearchSessions bottleneck

The contact record carries the session pointer directly:

```json
"WisdomInfo": {
  "SessionArn": "arn:aws:wisdom:eu-west-2:101506645078:session/<assistantId>/<sessionId>",
  "AiAgents": [{ "AiUseCase": "SelfService",
                 "AiAgentVersionId": "aa37e892-...:3",
                 "AiAgentEscalated": false }]
}
```

Present on **28 of 28** contacts. So the drill-down path is:

```
contact record -> WisdomInfo.SessionArn -> parse assistantId + sessionId -> ListSpans
```

`SearchSessions` (10 TPS, non-adjustable) is **not required**. Supersedes the
bridge described in §3b. `AiAgentEscalated` also gives escalation without touching
`ai_session` or the span.

### C3 — Chars-per-token calibrated: 3.63, not 4.0

Regressed `usage_input_tokens + cache_read + cache_write` on
`len(system_instructions) + len(input_messages)`, n=132:

```
slope     = 0.27519 tokens/char  ->  3.63 chars per token
intercept = 403 tokens (fixed tool-definition overhead)
R2        = 0.971
```

The naive `len/4` **underestimates by ~15%** (median −14.6%). Corrected
system-prompt sizes:

| Agent | chars | calibrated tokens | naive ÷4 |
|---|---|---|---|
| Bet365-Agent-Assist-Agent-Scoped | 31,850 | **8,765** | 7,962 |
| tagalog-support | 19,658 | **5,410** | 4,914 |
| demo-estonian | 17,876 | **4,919** | 4,469 |
| Bet365-Self-Service-Agent | 14,179 | **3,902** | 3,545 |
| Veolia-Self-Service-Agent | 11,127 | **3,062** | 2,782 |
| Veolia-Waste-SelfService-Agent | 10,031 | **2,760** | 2,508 |

Use 3.63 and add the 403-token intercept for any prompt-size estimate.

### C4 — Reasoning is 49.4% of output characters, not 28.2%

I previously reported the **message-count** share. Measured by characters:

| `values[].type` | count | chars | char share |
|---|---|---|---|
| text | 237 | 34,581 | 50.6% |
| reasoning | 93 | 33,784 | **49.4%** |
| tool_use | 33 | 0 | — |
| tool_result | 33 | 0 | — |

**Roughly half of all generated output is reasoning the customer never sees.**
Message count understated it by 21 points (28.2% vs 49.4%) because reasoning
blocks are long.

Latency implication, applying the char share to span output tokens:
22,415 output tokens × 49.4% ≈ 11,073 reasoning tokens ÷ 28 contacts ≈ 395 per
contact × 9.25 ms ≈ **3.7 seconds per contact spent generating reasoning**, out of
11.8s total model time. Internally consistent: 800 output tokens/contact × 9.25ms
= 7.4s of decode, half of which is reasoning.

This is the single largest actionable latency finding, and it is log-only —
`ai_prompt.output_token` is one total with no reasoning split.

Caveat: the char share is measured on `TRANSCRIPT_ORCHESTRATION_MESSAGE` bot
values and applied to span `usage_output_tokens`. Overlapping but not identical
populations, so treat the 3.7s as an estimate, not a measurement.

---

## 1e-ter. Contact Lens verification (read-only, no dataset association)

Verified directly from the Contact Lens S3 output — **no Lake Formation share, no
dataset association, no duplication.** Analysis objects live at the **bucket root**
under `Analysis/`, not under the `connect/<instance>/` prefix.

### Coverage: 15 of 28 contacts (54%)

| Instance | Analysed | Missing |
|---|---|---|
| `1de8296e` (voice self-service) | 7 | 6 |
| `cd54ca0b` (chat) | 5 | 7 |
| `cf6475f7` (chat) | 3 | 0 |

Voice self-service analysis lands under `Analysis/Voice/ivr/…` with
`Channel: "IVR"`. Chat under `Analysis/Chat/…`.

### The channels give DISJOINT measures

`ConversationCharacteristics` differs completely by channel:

| Channel | Present | Absent |
|---|---|---|
| **IVR / voice self-service** | `NonTalkTime` (+ per-instance offsets), `Interruptions`, `TalkTime` by participant, `TalkSpeed`, `TotalConversationDurationMillis` | **no `Sentiment`** |
| **CHAT** | `Sentiment` (overall by participant role, `SentimentShift` begin/end, per-transcript-group progressive score), `ResponseTime`, `ContactSummary` | **no `NonTalkTime`, no `Interruptions`, no `TalkTime`** |

**This breaks the single Section B design.** Sentiment correlation is possible only
for chat (7 contacts here); dead-air correlation only for voice (8 contacts). There
is no contact in this dataset where both exist. Section B must be split into two
channel-specific views, and no widget may blend them.

### Verified: model time is 74.8% of measured dead air

Cross-source reconciliation over the 7 analysed voice self-service contacts —
Contact Lens `NonTalkTime` (independent measurement) against summed `inference`
span durations:

```
total conversation time :  752.5s
non-talk (dead air)     :  126.6s  = 16.8% of conversation
measured model time     :   94.7s  = 74.8% of dead air
measured tool time      :    4.8s  =  3.8% of dead air
model + tool            :   99.4s  = 78.5% of dead air
unexplained             :   27.2s  = 21.5%
```

Per contact:

| contact | total | non-talk | model | turns | tokens |
|---|---|---|---|---|---|
| e70658f9 | 376.6s | 77.9s (21%) | 45.8s | 18 | 185,678 |
| a24dc847 | 236.4s | 22.7s (10%) | 31.0s | 13 | 100,321 |
| 006c160b | 67.9s | 18.2s (27%) | 10.8s | 6 | 37,472 |
| df42e540 | 29.6s | 4.3s (14%) | 2.7s | 2 | 11,840 |
| 8f57d794 | 15.4s | 3.6s (23%) | 1.3s | 1 | 5,927 |
| db87d2e1 | 14.2s | 0.0s (0%) | 1.6s | 1 | 5,923 |
| e046f9e3 | 12.3s | 0.0s (0%) | 1.4s | 1 | 5,932 |

**This is the business case, independently corroborated.** Roughly three quarters
of silence on a self-service voice call is the model thinking, and dead air is 16.8%
of total call duration — so model time is ~12.6% of billable voice duration.

The 9.25 ms/output-token coefficient also holds against this independent
measurement: 5,411 output tokens × 9.25ms = 50.1s = **53% of the 94.7s measured
model time**. Decode is the majority of model time, as predicted.

### Three honest caveats

1. **Not strictly additive.** `a24dc847` shows model time at 137% of non-talk.
   Contact Lens counts only silences exceeding 3 seconds, so inference calls under
   3s never register as dead air. Two single-turn contacts show 0% non-talk for the
   same reason. Treat 74.8% as an aggregate attribution, not a per-contact identity.
2. **Interruptions = 0 across all 7**, yet the spans record 2 `error_type=barge_in`
   events. Different definitions: the span flags speech detected during generation;
   Contact Lens `Interruptions` measures sustained overlapping talk. Both are valid;
   they are not interchangeable, and the span measure is the one tied to token waste.
3. **n=7.** Enough to establish the mechanism and the method. Not enough for a
   threshold.

---

## 1e-quater. Chat-side sentiment correlation — NEGATIVE RESULT

Ran the full join for all 7 analysed chat contacts. **Tokens do not correlate with
customer sentiment. Do not build this widget as designed.**

| contact | agent | cache | turns | tokens | out | sent CUST | sent SYS | esc |
|---|---|---|---|---|---|---|---|---|
| 96fc9446 | Bet365-Self-Service | ON | 4 | 62,632 | 1,602 | 0 | 0 | yes |
| 92b75da0 | Bet365-Agent-Assist | ON | 5 | 53,608 | 1,481 | −1 | 1.7 | yes |
| a23a6e2d | Bet365-Self-Service | ON | 4 | 52,506 | 1,545 | 0 | 0 | yes |
| 28090197 | Veolia-Waste-SelfService | off | 6 | 33,861 | 1,540 | 0 | 3 | yes |
| a91f072e | Veolia-Waste-SelfService | off | 5 | 29,329 | 1,108 | 1 | 1.7 | yes |
| 2ba7d965 | Veolia-Waste-SelfService | off | 3 | 19,425 | 622 | −1.7 | 0.8 | no |
| a8974e66 | Veolia-Waste-SelfService | off | 1 | 3,798 | 313 | 0 | 2.5 | yes |

Correlations against `OverallSentiment.CUSTOMER` (n=7):

```
total tokens        r = +0.043
output tokens       r = +0.211
turns               r = +0.201
model time          r = +0.130
transcript segments r = +0.285
```

All weak, and all **positive** — the opposite direction to the hypothesis that
heavier token burn signals an unhappy customer.

### Why it fails — three independent reasons

1. **Near-zero variance in the dependent variable.** Sentiment takes only 4 distinct
   values across 7 contacts (`−1.7, −1, 0, 1`), with **4 of 7 sitting at exactly 0**.
   You cannot correlate against a near-constant.
2. **The evidence points the other way.** The single *contained* contact (2ba7d965)
   has the **worst** sentiment (−1.7), while all six escalated contacts average 0.00.
   n=1 on the contained side, so this proves nothing — but it certainly does not
   support the hypothesis.
3. **n=7.** Critical |r| for significance at n=7 is ≈0.75. Nothing here is close.

### Also discovered: chat has no bot-side latency at all

`ResponseTime.DetailsByParticipantRole` returns `AGENT: None` on **all 7** chat
contacts. Only `CUSTOMER` average is populated (6,875ms → 137,947ms), which measures
how fast the *customer* replies — a behavioural signal, not a service metric.

So **Contact Lens provides no chat equivalent of `NonTalkTime`.** The voice
reconciliation (74.8% of dead air = model time) has no chat counterpart, and cannot
be constructed from Contact Lens.

### Revised channel positioning

| | Voice / IVR | Chat |
|---|---|---|
| Bot latency measure | ✅ `NonTalkTime`, corroborated at 74.8% model-attributable | ❌ none (`rt_agent` always null) |
| Customer sentiment | ❌ absent entirely | ⚠️ present but coarse and low-variance |
| Token / cache metrics | ✅ span-derived | ✅ span-derived |
| `ContactSummary` | ❌ | ✅ all 7 — useful for triage |
| Trace drill-down | ✅ voice only | ❌ documented voice-only |

**The validated business case is voice-centric.** For chat, drop the sentiment
linkage and use the data lake's LLM-evaluated `goal_success_rate` /
`is_handed_off` as the outcome variable instead — those are purpose-built for
resolution quality, whereas Contact Lens sentiment on short self-service chats is
too coarse to move.

### Data-quality note

Contact `75e8bd5e` (voice, non-IVR path) returns
`TotalConversationDurationMillis = 0` and `NonTalkTime = 0` despite 18,679ms of
measured model time. Excluded from the voice reconciliation. Any production build
needs a guard for zero-duration analysis records.

### Chat escalation rate is far higher than the blended figure

6 of these 7 chat contacts escalated (86%), against a blended 32% (9 of 28) across
all contacts. Escalation is heavily concentrated in chat. Another reason no widget
may blend channels.

---

## 1e-quinquies. Findings from implementation (tasks 1–7)

Three discoveries made while building against the real logs, all of which change
something in the spec.

### F1 — Log retention is shorter than the analysis window

Verified via `describe-log-groups`:

| Log group | Retention |
|---|---|
| `/aws/wisdom/AnyCompany-Fraud-Alerts-AgentAssist` | **14 days** |
| `/aws/wisdom/bet365-assistant-…` | **14 days** |
| `/aws/wisdom/tagalog-support` | **30 days** |

Re-pulling the corpus returned **928 events / 106 token-bearing spans** against
the original **1,205 / 132**. A quarter of the evidence base expired between
analysis and implementation.

Consequences:

1. **Level 0 can never look back further than retention.** A Logs-Insights-only
   deployment on a 14-day group has a 14-day maximum history, full stop.
2. **The default 14-day alarm baseline is exactly equal to the shortest
   retention**, leaving zero margin. On a 14-day group the baseline can only just
   fill, and any gap in delivery makes it unfillable. The Documentation must tell
   builders to raise log retention or shorten the baseline window.
3. **This is the strongest argument for Level 2** that the design did not
   previously make: the Parquet Span_Store is the only durable history. Level 2 is
   not merely "nicer analytics", it is the difference between having a baseline and
   not having one.

### F2 — The service added a span field after the survey

`trace_id` appears in the current logs and was not among the 43 fields originally
inventoried. The unknown-field tolerance (Requirement 2.10) handled it correctly —
retained, not a parse failure — and the canary test surfaced it. It is now in
`KNOWN_SPAN_FIELDS`. The canary stays as a drift detector rather than a
correctness gate.

Related fix: `*_timestamp` fields were not integer-typed, which silently made
`duration_ms` unavailable. The typing rule now covers `*_ms`, `*_timestamp`, the
token fields, `request_max_tokens` and `orchestration_iteration`. Version fields
stay text because they can carry `$LATEST`.

### F3 — The input-tokens-drive-latency claim is sample-unstable, not settled

This corrects §5 of this document.

| Corpus | raw r | significance | partial r controlling for output |
|---|---|---|---|
| Original, 132 spans | +0.151 | t = 1.74, **not** significant | +0.194 (weak) |
| Current, 106 spans | **+0.252** | **significant** at n=106 | **+0.290 (stronger)** |

Two samples from the same system give opposite answers, and controlling for output
tokens *strengthens* the relationship on the current corpus rather than collapsing
it. So the honest status is **unresolved**, not refuted.

What this does not change: output tokens remain the dominant driver by a wide
margin, so the 9.25 ms/output-token coefficient and every recommendation built on
it still stand. Input effects are second-order either way.

What it does change: Requirement 9.7 asserts as fact that input tokens do not
predict duration. That should be reworded to state that the effect is weak and
sample-dependent, and that the widget is excluded for that reason. Excluding it
remains right — a metric that reverses between samples should not be on a
dashboard — but the justification must be instability, not refutation.

**Design consequence.** A runtime gate cannot detect its own sample instability
from one sample. So this exclusion is a documented design decision, not something
`Statistical_Gate` enforces. What the gate *did* gain from this is a
**confounding rule**: partial correlation against a supplied control, which
suppresses a figure whose raw correlation collapses once a known stronger driver
is held constant. That rule is verified against synthetic data where the confound
is known by construction.

---

## 1f. Other sources worth joining

### Contact Lens conversational analytics (`contact_lens_conversational_analytics`)

**Explicitly documented as joining to AI Agent, AI Session, AI Prompt and AI Tool
on `contact_id`.** This supplies the outcome measures the token layer lacks.

| Column | What it gives |
|---|---|
| `sentiment_end_score_customer` | **CSAT proxy** — how the customer felt at the end |
| `sentiment_interaction_score_customer_without_agent` | **sentiment during the self-service portion only** — the AI agent's own sentiment impact |
| `sentiment_overall_score_customer` | whole-contact sentiment |
| `non_talk_time_total_ms` | **dead air, measured directly** — hold time plus silences >3s |
| `interruptions_customer_count` / `interruptions_time_customer_ms` | **barge-in, measured acoustically** |
| `talk_time_total_ms`, `total_conversation_duration_ms` | the real cost denominator |
| `response_time_average_customer_ms` | chat responsiveness |
| `categories` | Contact Lens rule matches |

This closes the CSAT gap that has been open since the first draft. **The highest-value
business widget — token burn vs customer sentiment — is now sourceable**, by joining
`ai_prompt` to `contact_lens_conversational_analytics` on `contact_id`.

It also reframes two of my widgets: `non_talk_time_total_ms` measures dead air as an
*outcome*, and `interruptions_customer_count` measures talk-over as an *outcome*.
TTFT and `error_type=barge_in` remain useful as *diagnostics* — they attribute the
cause to a specific model call — but they are no longer the primary measure.

Also available: `contact_record` (voice minutes, handle time, queue, disconnect
reason), `contact_evaluation_record` (QM scores incl. gen-AI automated answers),
`bot_conversations` / `bot_intents` / `bot_slots` (Lex layer), `contact_flow_events`.

### Cost Explorer — real billing units

Connect AI has **dedicated `ai-*` usage types**. Verified in this account,
18 Jul – 17 Aug:

| Usage type | Rate (derived) |
|---|---|
| `EUW2-ai-end-customer-mins` | $0.0382 / min |
| `EUW2-ai-chat-message` | $0.0100 / message |
| `USE1-ai-multi-region-end-customer-mins` | $0.0548 / min |
| `USW2-ai-multi-region-end-customer-mins` | $0.0549 / min |
| also present | `ai-guided-message`, `ai-email-message`, `ai-tasks` |

**Multi-region AI minutes carry a ~43% premium** ($0.0548 vs $0.0382), consistent
across two regions and two billing periods.

Use these as the cost basis instead of a hardcoded rate — they are the actual
billed units, so the dashboard reconciles with the invoice.

**Hypothesis worth testing, not asserted:** `ListModels` reports
`crossRegionStatus` as `REGIONAL` vs `GLOBAL`. If choosing a `global.*` model
routes usage to the `ai-multi-region-*` usage type, then model selection carries a
43% telephony cost consequence. The correlation is suggestive; "multi-region" may
instead refer to Connect global resiliency. Verify before acting.

### Two blind spots found in the unanalyzed events

**Reasoning-token share.** `TRANSCRIPT_ORCHESTRATION_MESSAGE.values[].type`
breaks down as text 237, **reasoning 93**, tool_use 33, tool_result 33. So
reasoning output is separately identifiable. `ai_prompt.output_token` is a single
total, so the reasoning share is **not derivable from the data lake** — and at
9.25 ms/output token, reasoning is paid for in dead air but never seen by the
customer. Genuinely new, log-only metric.

**Untracked ancillary model calls.** `TRANSCRIPT_LARGE_LANGUAGE_MODEL_INVOCATION`
shows **4 models**, not the 2 visible in trace spans:

```
eu.anthropic.claude-haiku-4-5     210
eu.anthropic.claude-sonnet-4-5      1
amazon.nova-lite-v1:0               1   BEDROCK_KB_QUERY_REFORMULATION
amazon.nova-pro-v1:0                1   GENERATIVE_INTENT_DETECTION
```

Query reformulation and intent detection invoke models but emit **no trace span
and no token counts** — only `prompt` / `completion` text. Median prompt is 18,630
chars (~4,700 est. tokens). This consumption is invisible to both the data lake and
the span path. Note `nova-pro` does not support prompt caching.

### Architectural constraint

`SearchSessions` is capped at **10 TPS** (service quota, non-adjustable). No
`ListSpans` quota is published. This hard-limits the contact-ID → session bridge to
drill-down use; it cannot backfill in bulk.

---

## 2. The actual gap — what this dashboard adds

Revised after the data lake check. **Three of the original seven gaps are closed
by `ai_prompt`.** What remains:

| # | Gap | Status | Evidence |
|---|---|---|---|
| ~~G1~~ | Token consumption | **CLOSED** | `ai_prompt.input_token` / `output_token` |
| G2 | **Prompt cache economics** | **open** | No cache column in `ai_prompt`; `AI_CACHE_READ_TOKENS` rejected by `GetMetricDataV2`. Absent everywhere |
| ~~G3~~ | Model attribution | **CLOSED** | `ai_prompt.model_id` |
| ~~G4~~ | Model comparison | **CLOSED** | follows from `model_id` |
| ~~G5~~ | Context growth per turn | **CLOSED** | derivable: `input_token` ordered by `creation_timestamp` within `ai_session_id` |
| G6 | **Instruction / prompt-size overhead** | **open** | `system_instructions` only in the span |
| G7 | **TTFT → dead air** | **open** | `time_to_first_token_ms` only in the span; data lake has total latency only |
| G8 | **True total tokens** | **open (new)** | `input_token + output_token` undercounts when caching is on. Only the span reconciles |
| G9 | **Near-real-time + alarms** | **open (new)** | Data lake is batch/daily. Minute-level alarming needs the log path |
| G10 | **Output ceiling & finish reasons** | **open** | `request_max_tokens`, `response_finish_reasons` absent from the table |
| G11 | Barge-in token waste | **narrowed** | Count is OOTB (`interruptions_customer_count`). Only *token attribution* of discarded work is unique |
| G12 | **Reasoning-token share** | **open (new)** | `values[].type='reasoning'` (93 of 396). `output_token` is a single total |
| G13 | **Ancillary model calls untracked** | **open (new)** | Query reformulation + intent detection emit no span and no token counts |
| ~~CSAT~~ | Customer sentiment | **CLOSED** | `contact_lens_conversational_analytics.sentiment_*`, joins on `contact_id` |
| ~~Dead air outcome~~ | Silence measurement | **CLOSED** | `non_talk_time_total_ms`. TTFT survives only as a diagnostic |

**Revised positioning.** This is no longer a token dashboard — Connect has one.
It is a **cache, context and reasoning-efficiency layer** that sits alongside the
data lake, plus the near-real-time alarming path the data lake cannot provide.

The strongest business widget is now a **join, not a build**: `ai_prompt` tokens
against `contact_lens_conversational_analytics.sentiment_end_score_customer` on
`contact_id`. That is the token-burn-vs-CSAT correlation, from two OOTB tables.

---

## 3. Data sources

### 3a. Connect Assistant logs (CloudWatch) — aggregation plane

`/aws/wisdom/<assistant-name>`, stream `QiCAssistantTranscript`. Enabled via
`PutDeliverySource` (`logType=EVENT_LOGS`) → `PutDeliveryDestination` →
`CreateDelivery`.

**Parsing trap.** `span` is documented as "JSON-serialized" but is a Java
`toString` map:

```
{span_id=abc, span_name=inference, usage_input_tokens=5823, response_finish_reasons=["end_turn"], ...}
```

Unquoted keys, values containing commas, nested JSON. `json.loads` fails.
Needs a depth-aware scanner splitting only at `, <key>=` at bracket depth 0.
In Logs Insights use `parse span "key=*," as alias`; note `output` is reserved.

### 3b. `qconnect:ListSpans` — drill-down plane

Verified **strict superset** of the log span: all 36 log fields plus `aiAgentId`,
`assistantId`, `sessionId`. Properly typed (`int`, `float`, `list`), ISO-8601
timestamps. Token values match logs exactly.

**More complete than logs.** In 2 of 29 sessions tested it returned an
`invoke_agent` span that never reached CloudWatch Logs.

**Cannot enumerate.** Takes only `assistantId` + `sessionId`. `SearchSessions`
supports only `EQUALS` (`PREFIX` rejected).

**Bridge:** `SearchSessions` `EQUALS` on a **contact ID** returns the session ID.
So **CTR contact ID → SearchSessions → sessionId → ListSpans** works with no
CloudWatch logging at all.

Retention: back to oldest available data (23 Jul), no expiry seen.

### 3c. `qconnect:ListModels` — model metadata

Supplies `supportsPromptCaching`, `modelLifecycle`, `crossRegionStatus`,
`endOfLifeTimestamp`, `supportedAIPromptTypes`. The only source for G3/G4 context.

### 3d. `connect:GetMetricDataV2` — join for normalisation

Used **only** to fetch OOTB denominators (`AI_INVOLVED_CONTACTS`,
`AI_HANDOFF_RATE`, `GOAL_SUCCESS_RATE`) so token metrics can be expressed per
contained contact. Do not re-derive these.

---

## 4. Field availability

### Token fields on `inference` spans

| Field | Coverage | Notes |
|---|---|---|
| `usage_input_tokens` | 132 | fresh (uncached) input |
| `usage_output_tokens` | 132 | |
| `usage_total_tokens` | 132 | **includes cache tokens** |
| `cache_read_input_tokens` | 18 | **absent entirely** when not caching |
| `cache_write_input_tokens` | 18 | **absent entirely** when not caching |
| `request_model` | 134 | only source of model attribution |
| `request_max_tokens` | 134 | all 2048 |
| `time_to_first_token_ms` | 132 | **not covered by any OOTB metric** |
| `system_instructions` | 134 | full prompt text |
| `temperature` | 134 | all 0.0 |
| `response_finish_reasons` | 132 | `end_turn` 90, `tool_use` 42 |

**Token identity — 0 mismatches in 132 spans:**

```
usage_total_tokens = usage_input_tokens + usage_output_tokens
                   + cache_read_input_tokens + cache_write_input_tokens
```

### Fields that do not exist (checked)

`guardrail_assessments` (0 of 296), `input_messages_truncated` (0 of 296),
escalation reason on `escalate_agent` (identity + timing only), tool name as a
field (`operation_name` is literally `execute_tool`; real name nested in
`input_messages.values[].toolUse.name`).

---

## 5. Verified coefficients

Seven tests: structural decomposition, multiple regression, partial correlation,
within-contact fixed effects, bootstrap CI, subgroup replication, significance.

| Coefficient | Value | Confidence |
|---|---|---|
| Duration per output token | **9.25 ms** | Bootstrap CI [7.94, 11.60]. Decode-time r=+0.870. 108 tok/sec. Holds in every subgroup n≥15 |
| TTFT per 1,000 input tokens | **33.6 ms** | Monotonic across all 5 quintiles (574→860ms). Excludes one 8,420ms cold-start outlier |
| Input tokens → total duration | **unusable** | t=1.74 not significant, CI crosses zero, sign flips across subgroups. **Do not build on this** |

Baselines: 4.71 inference calls/contact, 11.8s model time/contact, 4.2s
TTFT/contact, median 23,650 tokens/contact, p90 62,632, max 185,678.

**131 of 132 spans are `claude-haiku-4-5`.** These are Haiku numbers. Recompute
per model.

---

## 6. Widget specification

Src: **D** data lake (Athena), **L** logs, **S** ListSpans, **M** ListModels,
**G** GetMetricDataV2.

### 6.0 Source routing — read this first

| Widget group | Primary source | Why |
|---|---|---|
| Token volume, per-contact tokens, model attribution, model comparison, context growth, version scorecard | **D — data lake `ai_prompt`** | Native columns. Cross-instance. Joins to contact records |
| Cache economics (Tab 2) | **L — logs** | No cache columns exist in the data lake |
| TTFT (1.6), instruction overhead (2.5) | **L — logs** | Span-only fields |
| Total-token reconciliation (G8) | **L — logs** | Data lake undercounts when caching is active |
| Live alarms | **L — logs** | Data lake is daily batch |
| Per-contact forensics | **S / Contact details** | Full typed trace |
| Model caching + EOL metadata | **M — ListModels** | Join on `model_id` |
| Denominators (contained contacts) | **D `ai_session` or G** | `is_handed_off`, `goal_success_rate` |

**Rule: if a column exists in `ai_prompt`, query the data lake. Only fall back to
log parsing for the fields the data lake does not carry.** The tables below mark
each widget accordingly; widgets marked **D** need no span parsing at all.

Tabs 1, 3 and 4 below are **data-lake-first** (SQL in Athena, surfaced in
QuickSight or the CloudWatch dashboard). Tabs 2 and 5 are the log-derived
supplement that has no alternative source.

### Tab 1 — Token economics (G1, G7)

| # | Widget | Computation | Src | V |
|---|---|---|---|---|
| 1.1 | Tokens per contained contact | `Σ usage_total_tokens / AI_INVOLVED_CONTACTS×(1−AI_HANDOFF_RATE)` | L+G | verified — median 23,650 |
| 1.2 | Token composition | Stacked: fresh input / output / cache read / cache write | L | verified — 89.1% / 2.5% / 8.4% |
| 1.3 | Token distribution | p50 / p90 / max per contact, with outlier table | L | verified — 23,650 / 62,632 / 185,678 |
| 1.4 | Model time → billable minutes | `Σ inference duration / 60000 × voice_rate` | L | verified — 11.8s/contact |
| 1.5 | Output-token latency cost | `avg output tokens × 9.25ms` per agent | L | verified coefficient |
| 1.6 | TTFT (dead air) | p50/p90/p99 `time_to_first_token_ms` × calls/contact | L | verified — 4.2s/contact. **Distinct from `AVG_AI_PROMPT_INVOCATION_LATENCY`** |

1.6 note: OOTB gives *total* prompt latency. TTFT is the sub-measure that
represents silence before speech and exists only in the span.

### Tab 2 — Cache economics (G2)

| # | Widget | Computation | Src | V |
|---|---|---|---|---|
| 2.1 | Cache state per agent | Field **presence**, rendered ON / OFF / MIXED | L | verified — 4 of 6 agents OFF |
| 2.2 | Cache hit ratio | `cache_read / (cache_read + usage_input_tokens)` | L | verified — 40.5% where active |
| 2.3 | Uncached volume exposure | % of total tokens on agents with caching OFF | L | verified — **81%** |
| 2.4 | Cache warm-up profile | cache_write vs cache_read by turn ordinal | L | verified pattern below |
| 2.5 | Instruction overhead (G6) | `len(system_instructions)` → est. tokens × calls/contact | L+S | verified — 2,508–7,962 est. tokens |

Observed when active (Bet365):

```
turn 1:  fresh_in   488   cache_write 4,301   cache_read     0
turn 2:  fresh_in 4,483   cache_write     0   cache_read 4,301
turn 3:  fresh_in 4,784   cache_write     0   cache_read 4,301
```

**2.1 is presence-based, not value-based.** When inactive the fields are absent,
not zero. `SUM()` over a missing field returns 0, indistinguishable from
"cached nothing." Test presence explicitly.

**Open question, not a recommendation.** Ruled out as causes of the difference:
model (identical `claude-haiku-4-5`), prompt size (the non-caching agent has the
*larger* prompt: 19,658 vs 14,179 chars), `cachePoint` in template (absent from
both), date-based rollout (Veolia ran 47 uncached spans on the same days Bet365
cached). No customer-facing toggle found in `GetAIAgent` or `GetAIPrompt`.
Present this tab as evidence to raise with the service team.

### Tab 3 — Context window (G5)

| # | Widget | Computation | Src | V |
|---|---|---|---|---|
| 3.1 | Context growth curve | Median `usage_input_tokens` by turn ordinal within contact | L | verified — turn 1 4,251 → turn 12 10,122 |
| 3.2 | Context headroom | input tokens as % of model context window | L+M | derived |
| 3.3 | Output ceiling headroom | `usage_output_tokens` vs `request_max_tokens`, count ≥90% | L | verified — all 2048, **0 truncations** |
| 3.4 | Tokens per agent version | `usage_total_tokens` by `ai_agent_version` + `prompt_version` | L | verified — Bet365 agent v16 / prompt v12 |

3.4 is the token-only slice. Non-token version metrics are in the OOTB AI agents
performance table — link out, don't rebuild.

### Tab 4 — Model attribution & comparison (G3, G4)

| # | Widget | Computation | Src | V |
|---|---|---|---|---|
| 4.1 | Model inventory | `ListModels` joined to observed `request_model` | M+L | verified — 12 available, 2 in use |
| 4.2 | Token volume by model | tokens, calls, in/out split, cache % per `request_model` | L | verified |
| 4.3 | Model scorecard | per model: tokens/call, median duration, median TTFT, **own** ms/output-token | L | verified for Haiku (n=131) |
| 4.4 | Model A/B compare | normalised per 1,000 contained contacts | L+G | **valid, data insufficient** |
| 4.5 | Caching & EOL exposure | token volume where `supportsPromptCaching=false` or `modelLifecycle=LEGACY` | M+L | verified — 0 volume on EOL |
| 4.6 | Agent → model map | agent, versions, model, use case, calls, tokens, cache state | L | verified |

**Models available (`ListModels`, eu-west-2):**

| Model | Cache | Orch | Lifecycle | In use |
|---|---|---|---|---|
| eu.anthropic.claude-haiku-4-5 | yes | yes | ACTIVE | **yes** |
| eu.anthropic.claude-sonnet-4-5 | yes | yes | ACTIVE | **yes** |
| global.anthropic.claude-haiku-4-5 | yes | yes | ACTIVE | no |
| global.anthropic.claude-sonnet-4-5 | yes | yes | ACTIVE | no |
| global.anthropic.claude-sonnet-4-6 | yes | yes | ACTIVE | no |
| amazon.nova-lite-v1 | yes | yes | ACTIVE | no |
| amazon.nova-pro-v1 | **no** | yes | ACTIVE | no |
| global.amazon.nova-2-lite-v1 | **no** | yes | ACTIVE | no |
| openai.gpt-oss-20b | **no** | yes | ACTIVE | no |
| openai.gpt-oss-120b | **no** | yes | ACTIVE | no |
| anthropic.claude-3-7-sonnet | **no** | yes | **LEGACY** | no |
| anthropic.claude-3-haiku | **no** | **no** | **LEGACY** | no |

12 models, 6 cache-capable, 11 orchestration-capable, 2 in use.
EOL: claude-3-7-sonnet **2026-04-28 (passed)**, claude-3-haiku **2026-09-17**.

**In use:**

| Model | Calls | Tokens | avg/call | median dur | median TTFT |
|---|---|---|---|---|---|
| claude-haiku-4-5 | 131 | 888,496 | 6,782 | 2,295ms | 724ms |
| claude-sonnet-4-5 | 1 | 10,946 | 10,946 | 5,078ms | 1,773ms |

**4.4 gating.** Sonnet has one call. The apparent 2.2× duration / 2.4× TTFT gap
is a single data point. Require **n≥30 per model**; below that render
"insufficient data — n=X". Compute ms-per-output-token **inside** each column.

**Fair-comparison rule.** Never compare raw token totals — that measures traffic
mix. Normalise to **per 1,000 contained contacts** so a model that resolves in
fewer turns wins even if more verbose per turn.

### Tab 5 — Coverage & rollup

| # | Widget | Computation | Src | V |
|---|---|---|---|---|
| 5.1 | Cross-instance token rollup | tokens by `instance_arn` and region | L | verified — 3 instances in eu-west-2 |
| 5.2 | Logging coverage | `DescribeDeliverySources` vs `ListAssistants` per region | API | verified — **us-east-1: 5 assistants, 0 deliveries** |
| 5.3 | Barge-in token waste | tokens on `inference` spans with `status=ERROR AND error_type=barge_in` | L | verified — 2 events |

5.1 exists because `GetMetricDataV2` is single-instance scoped.
5.3 is narrow: traces already show barge-in per contact via the ERR label; this
widget quantifies the **tokens discarded**, which OOTB does not.

---

## 7. Widget → action

| Widget | Reads today | Action |
|---|---|---|
| 1.1 Tokens/contained contact | 23,650 median | Rising while `GOAL_SUCCESS_RATE` flat → prompt bloat |
| 1.2 Composition | input 89.1% | Input-dominant → caching is the lever, go to Tab 2 |
| 1.3 Distribution | max 7.8× median | Investigate the tail via ListSpans |
| 1.5 Output latency cost | 9.25 ms/token | Set response-length budget. −100 tokens = **−4.4s/contact** |
| 1.6 TTFT | 4.2s/contact | p90 breach → shorten responses. Closest CSAT proxy not in OOTB |
| 2.1/2.3 Cache state | **81% uncached** | Raise with service team — no toggle found |
| 2.5 Instruction overhead | 2,508–7,962 tokens/turn | Trim. Worth ~33.6ms per 1,000 tokens |
| 3.1 Context growth | 4,251 → 10,122 | Steepening → add history summarisation |
| 3.3 Output ceiling | 0 truncations | Truncations appear → raise `max_tokens` |
| 3.4 Tokens by version | agent v16 | Tokens up + `GOAL_SUCCESS_RATE` down → roll back |
| 4.5 Caching/EOL exposure | 0 today | Any volume → dated migration |
| 4.4 Model compare | n=1 Sonnet | Below n=30 shows "insufficient data" — correct answer |
| 5.2 Coverage | IAD: 0 deliveries | Enable delivery, or use CTR→ListSpans path |
| 5.3 Barge-in waste | 2 events | Rising → responses too slow or long |

---

## 8. Alarms

Three. Only on gaps OOTB cannot alarm on.

| Alarm | Metric | Threshold |
|---|---|---|
| Context leak | tokens/contact p90 week over week | +20% |
| Dead air | TTFT p90 by agent | from 2-week baseline |
| Cache regression | cache hit ratio drop, or agent flipping ON→OFF | any change |

Escalation, tool failure and success-rate alarms belong on OOTB metrics.
Do not hardcode thresholds from this sample: 28 contacts, 7 non-contiguous days,
3 of 6 agents at ≤5 calls.

---

## 9. Architecture

```
/aws/wisdom/* log groups
  -> subscription filter -> Lambda (depth-aware span parser)
  -> EMF metrics, dimensions: request_model, ai_agent_name, ai_agent_version,
     prompt_version, ai_agent_orchestrator_use_case, instance_arn
  -> CloudWatch metrics (1-min) -> dashboard + alarms

Normalisation: connect:GetMetricDataV2 (AI_INVOLVED_CONTACTS, AI_HANDOFF_RATE,
  GOAL_SUCCESS_RATE) -> per-contained-contact denominators
Model metadata: qconnect:ListModels -> caching / lifecycle / context window
Drill-down: contact_id -> SearchSessions(EQUALS) -> sessionId -> ListSpans
Deep dive: link out to Contact details > Automated Interaction tab
```

Cross-region: emit in-region, aggregate with per-widget region settings.

---

## 10. Excluded

| Not building | Why |
|---|---|
| Anything in §1d | Already OOTB |
| Token metrics from log parsing where `ai_prompt` has the column | `input_token`, `output_token`, `model_id`, `invocation_latency_ms`, versions are native data lake columns. Parsing spans for these is duplication |
| A custom trace viewer | Contact details > Automated Interaction tab already has one |
| Tool-name extraction from nested JSON | `ai_tool.ai_tool_name` is a first-class data lake column |
| Token cost in dollars | Connect never bills per token; will not reconcile with the invoice |
| Quality / accuracy scoring | OOTB `GOAL_SUCCESS_RATE`, `FAITHFULNESS_SCORE`, `COMPLETENESS_SCORE`, tool accuracy scores are LLM-evaluated and better than anything derivable from spans |
| Escalation reason ranking | No reason field exists; OOTB accuracy scores answer it properly |
| Input-tokens-drive-latency widget | Not statistically significant, sign flips |
| Guardrail widget | `guardrail_assessments` 0 of 296 spans |
| Intent analysis | 1 intent event in 1,205 |

---

## 11. Prerequisites

0. **Associate the 5 AI data lake datasets** (`ai_prompt`, `ai_session`,
   `ai_agent`, `ai_tool`, `ai_agent_knowledge_base`) via
   `BatchAssociateAnalyticsDataSet`. Currently only
   `contact_lens_conversational_analytics` is associated, on one instance. This is
   the highest-priority step — it delivers tokens and model attribution with no
   code. **Note: this creates a Lake Formation resource share, so confirm before
   running.**
1. **Enable the OOTB dashboard first.** Requires a security profile permission
   (AI agent view / AI prompt view / AI guardrails view / Connect assistant view)
   plus Access metrics or Dashboard access. This dashboard is a supplement, not a
   replacement.
1b. **Verify `ai_prompt.input_token` against the span.** Unconfirmed whether it
   equals `usage_input_tokens` (fresh input) or includes cache reads. This
   determines whether G8 is a real gap or a labelling issue. Requires the dataset
   association first.
2. **Enable AI agent traces** for drill-down: call recording, *Enable Bot
   Analytics, Transcripts, and AI Agent Traces*, *Enable Automated Interaction
   Logs*. Re-toggle if enabled before 5 Jun 2026.
3. **Enable assistant logging in us-east-1** — 5 assistants, 0 deliveries. Or use
   the CTR → SearchSessions → ListSpans path.
4. **Collect 2+ weeks** before setting thresholds.
5. **Recompute coefficients per model** as traffic spreads beyond Haiku 4.5.
