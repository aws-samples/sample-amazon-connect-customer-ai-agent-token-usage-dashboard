"""Channel_Resolver.

Channel is absent from every Assistant_Log event — all 32 field names across the
validated corpus, and none of them is the channel. It has to come from
``connect:DescribeContact``.

That matters more than it sounds. Voice and chat behave differently enough that a
blended aggregate is actively misleading: chat escalated at 86% against 32%
blended, and the Contact Lens measures available for each channel are disjoint
(voice has silence and interruptions but no sentiment; chat has sentiment but no
bot-side latency at all).

Two efficiencies fall out of the same API call:

- ``WisdomInfo.SessionArn`` carries both the assistant and session identifier, so
  ``qconnect:SearchSessions`` is never needed. It is capped at 10 TPS with no
  adjustment available, which would throttle any backfill.
- ``WisdomInfo.AiAgents[].AiAgentEscalated`` gives escalation without a second
  source.

One call per contact, not per span. At 4.71 spans per contact that is a 4.7x
reduction, which is why the cache is part of the component rather than an
optimisation bolted on later.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from . import constants as C

_SESSION_ARN = re.compile(
    r"^arn:aws[a-z\-]*:wisdom:[a-z0-9\-]+:\d{12}:session/"
    r"(?P<assistant>[0-9a-fA-F\-]{36})/(?P<session>[0-9a-fA-F\-]{36})$"
)

SUCCESS_TTL_SECONDS = 90 * 24 * 3600
"""Channel never changes for a contact, so a hit can be cached indefinitely."""

NEGATIVE_TTL_SECONDS = 15 * 60
"""Short, deliberately.

Long enough that a transient Connect failure does not re-storm the API for every
subsequent span of the same contact. Short enough that the contact becomes
resolvable again once the failure clears, rather than being poisoned for 90 days.
"""


@dataclass(frozen=True)
class ChannelInfo:
    contact_id: str
    channel: str
    assistant_id: str | None = None
    session_id: str | None = None
    escalated: bool | None = None
    resolved: bool = True

    @property
    def can_drill_down(self) -> bool:
        """ListSpans needs both identifiers; without them the control is disabled."""
        return self.assistant_id is not None and self.session_id is not None

    @classmethod
    def unresolved(cls, contact_id: str) -> "ChannelInfo":
        return cls(
            contact_id=contact_id, channel=C.CHANNEL_UNRESOLVED, resolved=False
        )


def parse_session_arn(arn: str | None) -> tuple[str | None, str | None]:
    """Extract (assistant_id, session_id) from a Wisdom session ARN."""
    if not arn:
        return (None, None)
    m = _SESSION_ARN.match(arn.strip())
    if not m:
        return (None, None)
    return (m.group("assistant"), m.group("session"))


class ChannelCache(Protocol):
    def get(self, contact_id: str) -> ChannelInfo | None: ...
    def put(self, info: ChannelInfo, ttl_seconds: int) -> None: ...


class InMemoryChannelCache:
    """Used in tests and as the per-invocation cache in front of DynamoDB."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[ChannelInfo, float]] = {}

    def get(self, contact_id: str) -> ChannelInfo | None:
        entry = self._store.get(contact_id)
        if entry is None:
            return None
        info, expires = entry
        if expires < time.time():
            del self._store[contact_id]
            return None
        return info

    def put(self, info: ChannelInfo, ttl_seconds: int) -> None:
        self._store[info.contact_id] = (info, time.time() + ttl_seconds)


class DynamoChannelCache:
    """DynamoDB-backed cache. TTL is enforced by the table, not by this code."""

    def __init__(self, table_name: str, client: Any) -> None:
        self._table = table_name
        self._client = client

    def get(self, contact_id: str) -> ChannelInfo | None:
        resp = self._client.get_item(
            TableName=self._table, Key={"contact_id": {"S": contact_id}}
        )
        item = resp.get("Item")
        if not item:
            return None

        def s(key: str) -> str | None:
            v = item.get(key, {})
            return v.get("S") if "S" in v else None

        def b(key: str) -> bool | None:
            v = item.get(key, {})
            return v.get("BOOL") if "BOOL" in v else None

        return ChannelInfo(
            contact_id=contact_id,
            channel=s("channel") or C.CHANNEL_UNRESOLVED,
            assistant_id=s("assistant_id"),
            session_id=s("session_id"),
            escalated=b("escalated"),
            resolved=(s("channel") or C.CHANNEL_UNRESOLVED) != C.CHANNEL_UNRESOLVED,
        )

    def put(self, info: ChannelInfo, ttl_seconds: int) -> None:
        item: dict[str, Any] = {
            "contact_id": {"S": info.contact_id},
            "channel": {"S": info.channel},
            "ttl": {"N": str(int(time.time()) + ttl_seconds)},
        }
        if info.assistant_id:
            item["assistant_id"] = {"S": info.assistant_id}
        if info.session_id:
            item["session_id"] = {"S": info.session_id}
        if info.escalated is not None:
            item["escalated"] = {"BOOL": info.escalated}
        self._client.put_item(TableName=self._table, Item=item)


@dataclass
class ResolverStats:
    cache_hits: int = 0
    api_calls: int = 0
    retries: int = 0
    unresolved: int = 0
    missing_session_arn: int = 0


class ChannelResolver:
    def __init__(
        self,
        connect_client: Any,
        cache: ChannelCache | None = None,
        max_attempts: int = 3,
        sleep: Any = time.sleep,
    ) -> None:
        self._connect = connect_client
        self._cache = cache or InMemoryChannelCache()
        self._max_attempts = max_attempts
        self._sleep = sleep
        self.stats = ResolverStats()

    def resolve(self, contact_id: str, instance_id: str) -> ChannelInfo:
        cached = self._cache.get(contact_id)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached

        info = self._describe(contact_id, instance_id)
        ttl = SUCCESS_TTL_SECONDS if info.resolved else NEGATIVE_TTL_SECONDS
        self._cache.put(info, ttl)
        return info

    def _describe(self, contact_id: str, instance_id: str) -> ChannelInfo:
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                self.stats.api_calls += 1
                resp = self._connect.describe_contact(
                    InstanceId=instance_id, ContactId=contact_id
                )
                return self._to_info(contact_id, resp.get("Contact", {}))
            except Exception as exc:  # boto3 raises many distinct types
                last_error = exc
                if attempt < self._max_attempts:
                    self.stats.retries += 1
                    self._sleep(min(2 ** (attempt - 1), 4))

        self.stats.unresolved += 1
        return ChannelInfo.unresolved(contact_id)

    def _to_info(self, contact_id: str, contact: dict) -> ChannelInfo:
        channel = contact.get("Channel")
        if not channel:
            self.stats.unresolved += 1
            return ChannelInfo.unresolved(contact_id)

        wisdom = contact.get("WisdomInfo") or {}
        assistant_id, session_id = parse_session_arn(wisdom.get("SessionArn"))
        if assistant_id is None or session_id is None:
            self.stats.missing_session_arn += 1

        agents = wisdom.get("AiAgents") or []
        escalated = (
            any(bool(a.get("AiAgentEscalated")) for a in agents) if agents else None
        )

        return ChannelInfo(
            contact_id=contact_id,
            channel=channel,
            assistant_id=assistant_id,
            session_id=session_id,
            escalated=escalated,
            resolved=True,
        )
