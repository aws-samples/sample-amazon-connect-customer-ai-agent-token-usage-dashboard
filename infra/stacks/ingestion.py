"""IngestionStack — Level 1 + Level 2 resources.

Creates:
  - Lambda (span parser + channel resolver + metric publisher + Firehose writer)
  - DynamoDB table (channel cache, on-demand, TTL enabled)
  - Subscription filter per assistant log group
  - Firehose delivery stream -> S3 (Parquet via Glue)
  - Glue database + table
  - S3 bucket (Span_Store)
  - CloudWatch dashboard (gap metrics only)
  - 3 CloudWatch alarms (placeholder thresholds — baseline-derived after 14 days)
  - Athena named queries (Level 2 analytics)
"""

from __future__ import annotations

import os
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_athena as athena,
    aws_cloudwatch as cloudwatch,
    aws_dynamodb as dynamodb,
    aws_glue as glue,
    aws_iam as iam,
    aws_kinesisfirehose as firehose,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_logs_destinations as log_dest,
    aws_s3 as s3,
)
from constructs import Construct

SRC_DIR = Path(__file__).resolve().parents[2] / "src"


class IngestionStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        assistant_log_groups: list[str],
        connect_instance_ids: list[str],
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -- S3 access-logs bucket -------------------------------------------
        # Dedicated target for SpanStore server access logs (CKV_AWS_18).
        access_logs_bucket = s3.Bucket(
            self,
            "SpanStoreAccessLogs",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        # -- S3 Span_Store ---------------------------------------------------
        bucket = s3.Bucket(
            self,
            "SpanStore",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            # Explicit hardening (CKV_AWS_53/54/55/56, CKV_AWS_18):
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            server_access_logs_bucket=access_logs_bucket,
            server_access_logs_prefix="span-store-access-logs/",
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="IntelligentTiering",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                            transition_after=Duration.days(30),
                        )
                    ],
                )
            ],
        )

        # -- Glue catalog ----------------------------------------------------
        db = glue.CfnDatabase(
            self,
            "GlueDB",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name="connect_ai_token_efficiency"
            ),
        )

        table = glue.CfnTable(
            self,
            "SpansTable",
            catalog_id=self.account,
            database_name="connect_ai_token_efficiency",
            table_input=glue.CfnTable.TableInputProperty(
                name="spans",
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "json",
                    "has_encrypted_data": "false",
                    "compressionType": "none",
                },
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    location=f"s3://{bucket.bucket_name}/spans/",
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.openx.data.jsonserde.JsonSerDe",
                    ),
                    columns=[
                        glue.CfnTable.ColumnProperty(name="span_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="parent_span_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="span_name", type="string"),
                        glue.CfnTable.ColumnProperty(name="status", type="string"),
                        glue.CfnTable.ColumnProperty(name="error_type", type="string"),
                        glue.CfnTable.ColumnProperty(name="start_timestamp", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="end_timestamp", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="duration_ms", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="usage_input_tokens", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="usage_output_tokens", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="usage_total_tokens", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="cache_read_input_tokens", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="cache_write_input_tokens", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="has_cache_fields", type="boolean"),
                        glue.CfnTable.ColumnProperty(name="reconciliation_state", type="string"),
                        glue.CfnTable.ColumnProperty(name="time_to_first_token_ms", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="request_model", type="string"),
                        glue.CfnTable.ColumnProperty(name="request_max_tokens", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="response_finish_reasons", type="string"),
                        glue.CfnTable.ColumnProperty(name="system_instructions_chars", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="chars_text", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="chars_reasoning", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="chars_tool", type="bigint"),
                        glue.CfnTable.ColumnProperty(name="tokens_reasoning", type="double"),
                        glue.CfnTable.ColumnProperty(name="tokens_text", type="double"),
                        glue.CfnTable.ColumnProperty(name="tokens_tool", type="double"),
                        glue.CfnTable.ColumnProperty(name="ai_agent_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="ai_agent_name", type="string"),
                        glue.CfnTable.ColumnProperty(name="ai_agent_version", type="string"),
                        glue.CfnTable.ColumnProperty(name="ai_agent_type", type="string"),
                        glue.CfnTable.ColumnProperty(name="ai_agent_orchestrator_use_case", type="string"),
                        glue.CfnTable.ColumnProperty(name="prompt_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="prompt_version", type="string"),
                        glue.CfnTable.ColumnProperty(name="assistant_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="session_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="contact_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="initial_contact_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="instance_id", type="string"),
                        glue.CfnTable.ColumnProperty(name="channel", type="string"),
                        glue.CfnTable.ColumnProperty(name="escalated", type="boolean"),
                    ],
                ),
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name="dt", type="string"),
                ],
            ),
        )
        table.add_dependency(db)

        # -- DynamoDB channel cache ------------------------------------------
        cache_table = dynamodb.Table(
            self,
            "ChannelCache",
            partition_key=dynamodb.Attribute(
                name="contact_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
            # Point-in-time recovery (CKV_AWS_28). The table is a rebuildable
            # cache, so this is defence-in-depth rather than a hard requirement.
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )

        # -- Firehose --------------------------------------------------------
        firehose_role = iam.Role(
            self,
            "FirehoseRole",
            assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"),
        )
        bucket.grant_read_write(firehose_role)

        stream = firehose.CfnDeliveryStream(
            self,
            "SpanStream",
            delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=firehose.CfnDeliveryStream.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=bucket.bucket_arn,
                role_arn=firehose_role.role_arn,
                prefix="spans/dt=!{timestamp:yyyy-MM-dd}/",
                error_output_prefix="errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/",
                buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                    interval_in_seconds=300, size_in_m_bs=64
                ),
                compression_format="UNCOMPRESSED",  # JSON serde reads it
            ),
        )

        # -- Lambda ----------------------------------------------------------
        fn = lambda_.Function(
            self,
            "SpanParser",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="connect_ai_tokens.handler.handler",
            code=lambda_.Code.from_asset(str(SRC_DIR)),
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "DEPLOYMENT_LEVEL": "LEVEL_2",
                "CHANNEL_CACHE_TABLE": cache_table.table_name,
                "DELIVERY_STREAM_NAME": stream.ref,
                "CONNECT_INSTANCE_IDS": ",".join(connect_instance_ids),
                "ASSISTANT_LOG_GROUPS": ",".join(assistant_log_groups),
                "METRIC_NAMESPACE": "ConnectAI/TokenEfficiency",
            },
        )

        # IAM — least privilege per the design table
        cache_table.grant_read_write_data(fn)
        bucket.grant_write(fn)

        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["firehose:PutRecordBatch"],
                resources=[stream.attr_arn],
            )
        )

        # DescribeContact scoped to the configured instances only
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["connect:DescribeContact"],
                resources=[
                    f"arn:aws:connect:{self.region}:{self.account}:instance/{iid}/contact/*"
                    for iid in connect_instance_ids
                ],
            )
        )

        # -- Subscription filters --------------------------------------------
        for i, lg_name in enumerate(assistant_log_groups):
            lg = logs.LogGroup.from_log_group_name(self, f"LG{i}", lg_name)
            logs.SubscriptionFilter(
                self,
                f"Sub{i}",
                log_group=lg,
                destination=log_dest.LambdaDestination(fn),
                filter_pattern=logs.FilterPattern.literal("TRANSCRIPT_AI_AGENT_TRACE"),
            )

        # -- Query_Library (Level 0 — zero infrastructure) ----------------------
        from query_library import LOGS_INSIGHTS_QUERIES

        log_group_names = assistant_log_groups
        for query_id, query_def in LOGS_INSIGHTS_QUERIES.items():
            logs.CfnQueryDefinition(
                self,
                f"L0-{query_id}",
                name=query_def["name"],
                query_string=query_def["query"].strip(),
                log_group_names=log_group_names,
            )

        # -- Curated Views (custom resource) ------------------------------------
        from views_cr import HANDLER_CODE

        views_fn = lambda_.Function(
            self,
            "ViewsCreator",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="index.handler",
            code=lambda_.Code.from_inline(HANDLER_CODE),
            timeout=Duration.minutes(10),
            memory_size=256,
            environment={
                "DATABASE": "connect_ai_token_efficiency",
                "WORKGROUP": "primary",
            },
        )

        views_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                ],
                resources=["*"],
            )
        )
        views_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "glue:GetDatabase",
                    "glue:GetTable",
                    "glue:GetPartitions",
                    "glue:CreateTable",
                    "glue:UpdateTable",
                    "glue:DeleteTable",
                ],
                resources=[
                    f"arn:aws:glue:{self.region}:{self.account}:catalog",
                    f"arn:aws:glue:{self.region}:{self.account}:database/connect_ai_token_efficiency",
                    f"arn:aws:glue:{self.region}:{self.account}:table/connect_ai_token_efficiency/*",
                ],
            )
        )
        views_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:GetBucketLocation", "s3:GetObject", "s3:ListBucket", "s3:PutObject"],
                resources=[
                    bucket.bucket_arn,
                    f"{bucket.bucket_arn}/*",
                    # Athena query results bucket (default)
                    f"arn:aws:s3:::aws-athena-query-results-{self.account}-{self.region}",
                    f"arn:aws:s3:::aws-athena-query-results-{self.account}-{self.region}/*",
                ],
            )
        )

        views_cr = cdk.CustomResource(
            self,
            "CuratedViews",
            service_token=views_fn.function_arn,
            properties={
                # Force update when views change (hash of the handler code)
                "Version": "2024-08-24-v3-dedup-stableid",
            },
        )
        views_cr.node.add_dependency(table)

        # -- CloudWatch Alarms -----------------------------------------------
        # These cover ONLY gaps not alarmed by OOTB metrics.
        # Thresholds are placeholders — replace after 14 days of baseline.
        metric_namespace = "ConnectAI/TokenEfficiency"

        # Alarm 1: Context leak — tokens per contact rising
        # Fires when the p90 tokens/contact (approximated via TotalTokens sum)
        # exceeds the placeholder. In production, derive from your baseline.
        tokens_metric = cloudwatch.Metric(
            namespace=metric_namespace,
            metric_name="TotalTokens",
            statistic="Sum",
            period=Duration.hours(1),
        )
        contacts_metric = cloudwatch.Metric(
            namespace=metric_namespace,
            metric_name="Contacts",
            statistic="Sum",
            period=Duration.hours(1),
        )
        context_leak_alarm = cloudwatch.Alarm(
            self,
            "ContextLeakAlarm",
            alarm_name="ConnectAI-ContextLeak-TokensPerContact",
            alarm_description=(
                "Tokens per contact exceeds baseline threshold. "
                "Indicates context window growth (prompt bloat or history "
                "accumulation). Replace threshold after 14 days of baseline data."
            ),
            metric=cloudwatch.MathExpression(
                expression="total / contacts",
                using_metrics={
                    "total": tokens_metric,
                    "contacts": contacts_metric,
                },
                period=Duration.hours(1),
            ),
            # PLACEHOLDER: replace with your p90 + 20% after baseline
            threshold=50000,
            evaluation_periods=3,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # Alarm 2: TTFT dead air — p90 exceeding caller patience
        ttft_alarm = cloudwatch.Alarm(
            self,
            "TTFTAlarm",
            alarm_name="ConnectAI-TTFT-P90-DeadAir",
            alarm_description=(
                "Time-to-first-token p90 exceeds threshold. This directly "
                "measures dead air the caller hears before the agent speaks. "
                "Not available in OOTB metrics (AVG_AI_PROMPT_INVOCATION_LATENCY "
                "is total duration, not TTFT). Replace threshold from baseline."
            ),
            metric=cloudwatch.Metric(
                namespace=metric_namespace,
                metric_name="TimeToFirstTokenMs",
                statistic="p90",
                period=Duration.hours(1),
            ),
            # PLACEHOLDER: 3000ms = 3s, typical caller patience limit
            threshold=3000,
            evaluation_periods=3,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # Alarm 3: Cache regression — cache hit ratio dropping
        cache_alarm = cloudwatch.Alarm(
            self,
            "CacheRegressionAlarm",
            alarm_name="ConnectAI-CacheRegression",
            alarm_description=(
                "Cache hit ratio dropped below threshold. Could indicate: "
                "agent flipped from caching ON to OFF, cache eviction under "
                "load, or a model change to a non-caching model. No data lake "
                "equivalent exists (no cache columns in ai_prompt)."
            ),
            metric=cloudwatch.Metric(
                namespace=metric_namespace,
                metric_name="CacheHitRatio",
                statistic="Average",
                period=Duration.hours(1),
            ),
            # PLACEHOLDER: fires when ratio drops below 30% (where active)
            threshold=0.30,
            evaluation_periods=3,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # -- CloudWatch Dashboard --------------------------------------------
        from dashboard import build_dashboard_body

        dashboard = cloudwatch.CfnDashboard(
            self,
            "TokenEfficiencyDashboard",
            dashboard_name="ConnectAI-TokenEfficiency",
            dashboard_body=build_dashboard_body(self.region),
        )

        # -- Athena Named Queries --------------------------------------------
        from queries import QUERIES

        for query_id, query_def in QUERIES.items():
            athena.CfnNamedQuery(
                self,
                f"Query-{query_id}",
                database="connect_ai_token_efficiency",
                query_string=query_def["sql"].strip(),
                description=query_def["description"],
                name=f"connect-ai-{query_id}",
            )

        # -- Outputs ---------------------------------------------------------
        cdk.CfnOutput(self, "SpanStoreBucket", value=bucket.bucket_name)
        cdk.CfnOutput(self, "ChannelCacheTable", value=cache_table.table_name)
        cdk.CfnOutput(self, "DeliveryStream", value=stream.ref)
        cdk.CfnOutput(self, "ParserFunction", value=fn.function_name)
        cdk.CfnOutput(self, "DashboardName", value="ConnectAI-TokenEfficiency")
