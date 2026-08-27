#!/usr/bin/env python3
"""Create the CloudWatch dashboard with widgets for each metric group."""

import json

import boto3

REGION = "eu-west-2"
NAMESPACE = "ConnectAI/TokenEfficiency"
DASHBOARD_NAME = "ConnectAI-TokenEfficiency"

# Build the dashboard body
widgets = []
y = 0


def add_text(title, md, width=24, height=2):
    global y
    widgets.append({
        "type": "text", "x": 0, "y": y, "width": width, "height": height,
        "properties": {"markdown": md}
    })
    y += height


def add_log_query(title, query, log_groups, width=12, height=6):
    global y
    widgets.append({
        "type": "log", "x": 0, "y": y, "width": width, "height": height,
        "properties": {
            "title": title,
            "query": f"SOURCE {' | '.join(repr(lg) for lg in log_groups)}\n{query}",
            "region": REGION, "view": "table",
        }
    })
    y += height


def add_log_query_r(title, query, log_groups, width=12, height=6):
    global y
    widgets.append({
        "type": "log", "x": 12, "y": y - height, "width": width, "height": height,
        "properties": {
            "title": title,
            "query": f"SOURCE {' | '.join(repr(lg) for lg in log_groups)}\n{query}",
            "region": REGION, "view": "table",
        }
    })


LG = [
    "'/aws/wisdom/tagalog-support'",
    "'/aws/wisdom/bet365-assistant-bet365-demo-v3-AssistantStack-QX913GJCE6MQ'",
    "'/aws/wisdom/AnyCompany-Fraud-Alerts-AgentAssist'",
]
LGS = [
    "/aws/wisdom/tagalog-support",
    "/aws/wisdom/bet365-assistant-bet365-demo-v3-AssistantStack-QX913GJCE6MQ",
    "/aws/wisdom/AnyCompany-Fraud-Alerts-AgentAssist",
]

# -- OVERVIEW ----------------------------------------------------------------
add_text("Overview", "# 📊 AI Agent Token Efficiency Dashboard\n"
         "Token, cache, reasoning and latency efficiency for Amazon Connect AI agents. "
         "Channel-scoped — voice and chat are never blended.\n\n"
         "*Also available in the built-in AI Agent Performance dashboard:* "
         "invocation counts, success rates, quality scores, tool accuracy.")

add_log_query(
    "Token Summary (last 24h)",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "usage_total_tokens=*," as tot
| parse span "usage_input_tokens=*," as inp
| parse span "usage_output_tokens=*," as out_tok
| filter sn = "inference" and ispresent(tot)
| stats sum(tot) as total_tokens, sum(inp) as input_tokens, sum(out_tok) as output_tokens, count(*) as inference_calls""",
    LGS, width=12, height=4
)
add_log_query_r(
    "Cache State by Agent",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "ai_agent_name=*," as agent
| parse span "cache_read_input_tokens=*," as cr
| filter sn = "inference"
| stats count(*) as spans, count(cr) as with_cache by agent
| sort spans desc""",
    LGS, width=12, height=4
)

# -- WASTE -------------------------------------------------------------------
add_text("Waste & Efficiency", "## 🗑️ Waste & Efficiency\n"
         "81% of token volume ran with caching OFF in the validation sample. "
         "Roughly half of generated output is reasoning the customer never sees.")

add_log_query(
    "Reasoning vs Customer-Facing Output",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "ai_agent_name=*," as agent
| parse span "usage_output_tokens=*," as out_tok
| filter sn = "inference" and ispresent(out_tok)
| stats sum(out_tok) as output_tokens, count(*) as spans by agent
| sort output_tokens desc""",
    LGS, width=12, height=5
)
add_log_query_r(
    "Barge-in Discarded Tokens",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "status=*," as st
| parse span "error_type=*," as et
| parse span "usage_total_tokens=*," as tot
| filter sn = "inference" and st = "ERROR" and et = "barge_in"
| stats sum(tot) as discarded_tokens, count(*) as barge_in_events""",
    LGS, width=12, height=5
)

# -- LATENCY ----------------------------------------------------------------
add_text("Latency", "## ⏱️ Latency\n"
         "TTFT (time to first token) is the silence the caller hears. "
         "9.25 ms per output token, CI [7.94, 11.60]. "
         "Not available in any OOTB metric.")

add_log_query(
    "TTFT by Agent (p50/p90)",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "ai_agent_name=*," as agent
| parse span "time_to_first_token_ms=*," as ttft
| filter sn = "inference" and ispresent(ttft)
| stats pct(ttft, 50) as p50, pct(ttft, 90) as p90, pct(ttft, 99) as p99, count(*) as n by agent
| sort n desc""",
    LGS, width=12, height=5
)
add_log_query_r(
    "Model Time per Contact",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "start_timestamp=*," as ts0
| parse span "end_timestamp=*," as ts1
| parse span "initial_contact_id=*," as cid
| filter sn = "inference" and ispresent(ts0) and ispresent(ts1)
| stats sum(ts1 - ts0) as model_time_ms, count(*) as calls by cid
| stats avg(model_time_ms) as avg_model_ms, pct(model_time_ms, 90) as p90_model_ms, count(*) as contacts""",
    LGS, width=12, height=5
)

# -- CONVERSATION SHAPE ------------------------------------------------------
add_text("Conversation Shape", "## 🔄 Conversation Shape")

add_log_query(
    "Tokens per Contact (distribution)",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "usage_total_tokens=*," as tot
| parse span "initial_contact_id=*," as cid
| filter sn = "inference" and ispresent(tot) and ispresent(cid)
| stats sum(tot) as contact_tokens by cid
| stats pct(contact_tokens, 50) as p50, pct(contact_tokens, 90) as p90, max(contact_tokens) as worst, count(*) as contacts""",
    LGS, width=12, height=5
)
add_log_query_r(
    "Turns per Contact",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "initial_contact_id=*," as cid
| filter sn = "invoke_agent" and ispresent(cid)
| stats count(*) as turns by cid
| stats avg(turns) as avg_turns, pct(turns, 90) as p90_turns, count(*) as contacts""",
    LGS, width=12, height=5
)

# -- ATTRIBUTION -------------------------------------------------------------
add_text("Attribution", "## 🏷️ Attribution by Agent & Model")

add_log_query(
    "Tokens by Agent and Model",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "ai_agent_name=*," as agent
| parse span "request_model=*," as model
| parse span "usage_total_tokens=*," as tot
| filter sn = "inference" and ispresent(tot)
| stats sum(tot) as tokens, count(*) as calls by agent, model
| sort tokens desc""",
    LGS, width=24, height=6
)

# -- OUTCOMES ----------------------------------------------------------------
add_text("Outcomes", "## 🎯 Outcomes\n"
         "*Escalation, tool success and quality scores are also available in the "
         "built-in AI Agent Performance dashboard.*")

add_log_query(
    "Tool Success/Failure",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "status=*," as st
| parse span "error_type=*," as et
| filter sn = "execute_tool"
| stats count(*) as total, count_distinct(st = "OK") as success by st, et""",
    LGS, width=12, height=4
)

# -- DATA QUALITY ------------------------------------------------------------
add_text("Data Quality", "## 🔍 Data Quality & Coverage")

add_log_query(
    "Parse Failures & Reconciliation",
    """| filter event_type = "TRANSCRIPT_AI_AGENT_TRACE"
| parse span "span_name=*," as sn
| parse span "usage_total_tokens=*," as tot
| filter sn = "inference"
| stats count(*) as total_inference, count(tot) as with_tokens, count(*) - count(tot) as without_tokens""",
    LGS, width=24, height=4
)


dashboard_body = json.dumps({"widgets": widgets})

client = boto3.client("cloudwatch", region_name=REGION)
client.put_dashboard(DashboardName=DASHBOARD_NAME, DashboardBody=dashboard_body)
print(f"Dashboard '{DASHBOARD_NAME}' created/updated in {REGION}")
print(f"  https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#dashboards:name={DASHBOARD_NAME}")
