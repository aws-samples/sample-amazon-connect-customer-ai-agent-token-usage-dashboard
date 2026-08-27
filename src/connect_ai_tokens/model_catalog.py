"""Model_Catalog — snapshots qconnect:ListModels and joins to usage.

Surfaces:
- Which models support prompt caching
- Which are LEGACY or approaching end of life
- Token volume on non-caching or EOL models
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class ModelInfo:
    model_id: str
    supports_prompt_caching: bool
    model_lifecycle: str  # ACTIVE, LEGACY
    end_of_life_timestamp: str | None = None
    cross_region_status: str | None = None
    supports_orchestration: bool = False

    @property
    def is_legacy(self) -> bool:
        return self.model_lifecycle == "LEGACY"

    @property
    def days_to_eol(self) -> int | None:
        if not self.end_of_life_timestamp:
            return None
        try:
            eol = datetime.fromisoformat(
                self.end_of_life_timestamp.replace("Z", "+00:00")
            )
            return (eol - datetime.now(timezone.utc)).days
        except Exception:
            return None

    @property
    def eol_within_180_days(self) -> bool:
        d = self.days_to_eol
        return d is not None and d <= 180


def fetch_model_catalog(
    client: Any, assistant_id: str
) -> dict[str, ModelInfo]:
    """Call qconnect:ListModels and return a dict keyed by model_id."""
    resp = client.list_models(assistantId=assistant_id)
    models = {}
    for m in resp.get("modelSummaries", []):
        mid = m.get("modelId", "")
        models[mid] = ModelInfo(
            model_id=mid,
            supports_prompt_caching=m.get("supportsPromptCaching", False),
            model_lifecycle=m.get("modelLifecycle", "ACTIVE"),
            end_of_life_timestamp=m.get("endOfLifeTimestamp"),
            cross_region_status=m.get("crossRegionStatus"),
            supports_orchestration="ORCHESTRATION"
            in m.get("supportedAIPromptTypes", []),
        )
    return models
