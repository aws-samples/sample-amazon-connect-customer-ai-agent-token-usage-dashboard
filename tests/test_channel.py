"""Channel_Resolver tests."""

from __future__ import annotations

import pytest

from connect_ai_tokens import constants as C
from connect_ai_tokens.channel import (
    NEGATIVE_TTL_SECONDS,
    SUCCESS_TTL_SECONDS,
    ChannelResolver,
    InMemoryChannelCache,
    parse_session_arn,
)

REAL_ARN = (
    "arn:aws:wisdom:eu-west-2:101506645078:session/"
    "1522ee99-f6f3-4641-ad8e-89f7348c577d/17395249-611b-4056-abd9-cfa53ce64f87"
)


class FakeConnect:
    def __init__(self, responses=None, fail_times=0):
        self._responses = responses or {}
        self._fail_times = fail_times
        self.calls = 0

    def describe_contact(self, InstanceId, ContactId):  # noqa: N803
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("throttled")
        return self._responses.get(ContactId, {"Contact": {}})


def voice_contact(escalated=False):
    return {
        "Contact": {
            "Channel": "VOICE",
            "WisdomInfo": {
                "SessionArn": REAL_ARN,
                "AiAgents": [
                    {
                        "AiUseCase": "SelfService",
                        "AiAgentVersionId": "aa37e892-3325-4fa6-8def-de956d6d4eb7:3",
                        "AiAgentEscalated": escalated,
                    }
                ],
            },
        }
    }


# --------------------------------------------------------------------------


def test_parses_real_session_arn():
    assistant, session = parse_session_arn(REAL_ARN)
    assert assistant == "1522ee99-f6f3-4641-ad8e-89f7348c577d"
    assert session == "17395249-611b-4056-abd9-cfa53ce64f87"


@pytest.mark.parametrize(
    "arn",
    [
        None,
        "",
        "not-an-arn",
        "arn:aws:wisdom:eu-west-2:101506645078:assistant/abc",
        "arn:aws:wisdom:eu-west-2:101506645078:session/abc",
    ],
)
def test_rejects_malformed_session_arn(arn):
    assert parse_session_arn(arn) == (None, None)


def test_resolves_channel_and_session():
    connect = FakeConnect({"c1": voice_contact()})
    r = ChannelResolver(connect, sleep=lambda _: None)
    info = r.resolve("c1", "inst-1")
    assert info.channel == "VOICE"
    assert info.resolved
    assert info.can_drill_down
    assert info.escalated is False


def test_escalation_read_from_wisdom_info():
    connect = FakeConnect({"c1": voice_contact(escalated=True)})
    info = ChannelResolver(connect, sleep=lambda _: None).resolve("c1", "i")
    assert info.escalated is True


def test_one_api_call_per_contact_not_per_span():
    """4.71 spans per contact means this is a 4.7x call reduction."""
    connect = FakeConnect({"c1": voice_contact()})
    r = ChannelResolver(connect, cache=InMemoryChannelCache(), sleep=lambda _: None)
    for _ in range(10):
        r.resolve("c1", "inst-1")
    assert connect.calls == 1
    assert r.stats.cache_hits == 9


def test_retries_up_to_three_attempts_then_succeeds():
    connect = FakeConnect({"c1": voice_contact()}, fail_times=2)
    r = ChannelResolver(connect, sleep=lambda _: None)
    info = r.resolve("c1", "inst-1")
    assert info.resolved
    assert connect.calls == 3
    assert r.stats.retries == 2


def test_unresolved_after_three_failures():
    connect = FakeConnect({}, fail_times=99)
    r = ChannelResolver(connect, sleep=lambda _: None)
    info = r.resolve("c1", "inst-1")
    assert not info.resolved
    assert info.channel == C.CHANNEL_UNRESOLVED
    assert connect.calls == 3
    assert not info.can_drill_down


def test_negative_result_does_not_restorm_the_api():
    """A transient failure must not trigger a call for every later span."""
    connect = FakeConnect({}, fail_times=99)
    r = ChannelResolver(connect, cache=InMemoryChannelCache(), sleep=lambda _: None)
    for _ in range(5):
        r.resolve("c1", "inst-1")
    assert connect.calls == 3  # three attempts once, then cached


def test_negative_ttl_is_much_shorter_than_success_ttl():
    """So a cleared failure becomes resolvable again rather than poisoned."""
    assert NEGATIVE_TTL_SECONDS < SUCCESS_TTL_SECONDS / 100


def test_missing_channel_is_unresolved():
    connect = FakeConnect({"c1": {"Contact": {"WisdomInfo": {}}}})
    info = ChannelResolver(connect, sleep=lambda _: None).resolve("c1", "i")
    assert info.channel == C.CHANNEL_UNRESOLVED


def test_missing_session_arn_disables_drill_down_but_keeps_channel():
    connect = FakeConnect({"c1": {"Contact": {"Channel": "CHAT", "WisdomInfo": {}}}})
    r = ChannelResolver(connect, sleep=lambda _: None)
    info = r.resolve("c1", "i")
    assert info.channel == "CHAT"
    assert info.resolved
    assert not info.can_drill_down
    assert r.stats.missing_session_arn == 1


def test_search_sessions_is_never_called():
    """It is capped at 10 TPS, so the resolver must not depend on it."""
    import inspect

    from connect_ai_tokens import channel

    source = inspect.getsource(channel)
    assert "search_sessions" not in source
    assert "SearchSessions" not in source.replace(
        "qconnect:SearchSessions", ""
    ).replace("``SearchSessions``", "")
