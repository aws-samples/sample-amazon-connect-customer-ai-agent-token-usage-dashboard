"""Configuration loaded from environment (Lambda) or CDK context (synth).

No account, region, instance or assistant identifier is ever hardcoded
(Requirement 28.5). Everything resolves at run time or synth time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from . import constants as C


class DeploymentLevel(str, Enum):
    LEVEL_0 = "LEVEL_0"  # Query_Library + dashboard widgets only, no compute
    LEVEL_1 = "LEVEL_1"  # + span parser, EMF metrics, alarms
    LEVEL_2 = "LEVEL_2"  # + Span_Store, Curated_Views, BI


@dataclass(frozen=True)
class StatisticalConfig:
    """Thresholds governing whether a figure may render at all."""

    minimum_n: int = C.DEFAULT_MINIMUM_N
    significance_level: float = C.DEFAULT_SIGNIFICANCE_LEVEL
    bootstrap_resamples: int = C.DEFAULT_BOOTSTRAP_RESAMPLES
    minimum_distinct_values: int = C.DEFAULT_MINIMUM_DISTINCT_VALUES
    maximum_single_value_share: float = C.DEFAULT_MAXIMUM_SINGLE_VALUE_SHARE

    def __post_init__(self) -> None:
        if not 2 <= self.minimum_n <= 10_000:
            raise ValueError("minimum_n must be between 2 and 10000")
        if not 0.001 <= self.significance_level <= 0.100:
            raise ValueError("significance_level must be between 0.001 and 0.100")
        if not 0.0 < self.maximum_single_value_share <= 1.0:
            raise ValueError("maximum_single_value_share must be in (0, 1]")


@dataclass(frozen=True)
class Config:
    deployment_level: DeploymentLevel = DeploymentLevel.LEVEL_1
    assistant_log_groups: tuple[str, ...] = ()
    connect_instance_ids: tuple[str, ...] = ()
    baseline_window_days: int = C.DEFAULT_BASELINE_WINDOW_DAYS
    channel_cache_table: str | None = None
    delivery_stream_name: str | None = None
    metric_namespace: str = "ConnectAI/TokenEfficiency"
    describe_contact_timeout_s: float = 5.0
    describe_contact_max_attempts: int = 3
    statistics: StatisticalConfig = field(default_factory=StatisticalConfig)

    @property
    def emits_metrics(self) -> bool:
        return self.deployment_level in (
            DeploymentLevel.LEVEL_1,
            DeploymentLevel.LEVEL_2,
        )

    @property
    def persists_spans(self) -> bool:
        return self.deployment_level is DeploymentLevel.LEVEL_2

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        e = env if env is not None else dict(os.environ)

        def csv(key: str) -> tuple[str, ...]:
            raw = e.get(key, "").strip()
            return tuple(p.strip() for p in raw.split(",") if p.strip())

        def num(key: str, default, cast):
            raw = e.get(key)
            return cast(raw) if raw not in (None, "") else default

        return cls(
            deployment_level=DeploymentLevel(
                e.get("DEPLOYMENT_LEVEL", DeploymentLevel.LEVEL_1.value)
            ),
            assistant_log_groups=csv("ASSISTANT_LOG_GROUPS"),
            connect_instance_ids=csv("CONNECT_INSTANCE_IDS"),
            baseline_window_days=num(
                "BASELINE_WINDOW_DAYS", C.DEFAULT_BASELINE_WINDOW_DAYS, int
            ),
            channel_cache_table=e.get("CHANNEL_CACHE_TABLE") or None,
            delivery_stream_name=e.get("DELIVERY_STREAM_NAME") or None,
            metric_namespace=e.get("METRIC_NAMESPACE", "ConnectAI/TokenEfficiency"),
            statistics=StatisticalConfig(
                minimum_n=num("MINIMUM_N", C.DEFAULT_MINIMUM_N, int),
                significance_level=num(
                    "SIGNIFICANCE_LEVEL", C.DEFAULT_SIGNIFICANCE_LEVEL, float
                ),
                bootstrap_resamples=num(
                    "BOOTSTRAP_RESAMPLES", C.DEFAULT_BOOTSTRAP_RESAMPLES, int
                ),
                minimum_distinct_values=num(
                    "MINIMUM_DISTINCT_VALUES", C.DEFAULT_MINIMUM_DISTINCT_VALUES, int
                ),
                maximum_single_value_share=num(
                    "MAXIMUM_SINGLE_VALUE_SHARE",
                    C.DEFAULT_MAXIMUM_SINGLE_VALUE_SHARE,
                    float,
                ),
            ),
        )
