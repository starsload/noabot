from types import SimpleNamespace

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel


class _DummyChannel(BaseChannel):
    name = "dummy"
    _sent: list[OutboundMessage]

    def __init__(self, config, bus):
        super().__init__(config, bus)
        self._sent = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        self._sent.append(msg)


def test_is_allowed_requires_exact_match() -> None:
    channel = _DummyChannel(SimpleNamespace(allow_from=["allow@email.com"]), MessageBus())

    assert channel.is_allowed("allow@email.com") is True
    assert channel.is_allowed("attacker|allow@email.com") is False


def test_is_allowed_supports_dict_allow_from_alias() -> None:
    channel = _DummyChannel({"allowFrom": ["alice"]}, MessageBus())

    assert channel.is_allowed("alice") is True


def test_is_allowed_denies_empty_dict_allow_from() -> None:
    channel = _DummyChannel({"allow_from": []}, MessageBus())

    assert channel.is_allowed("alice") is False


def test_is_allowed_handles_none_allow_from() -> None:
    channel = _DummyChannel({"allow_from": None}, MessageBus())
    assert channel.is_allowed("alice") is False

    channel2 = _DummyChannel({"allowFrom": None}, MessageBus())
    assert channel2.is_allowed("alice") is False


def test_is_allowed_star_allows_all() -> None:
    channel = _DummyChannel({"allowFrom": ["*"]}, MessageBus())
    assert channel.is_allowed("anyone") is True


def test_is_allowed_pairing_fallback(monkeypatch) -> None:
    channel = _DummyChannel({"allowFrom": []}, MessageBus())
    monkeypatch.setattr(
        "nanobot.channels.base.is_approved", lambda _ch, sid: sid == "paired"
    )
    assert channel.is_allowed("paired") is True
    assert channel.is_allowed("unknown") is False


@pytest.mark.asyncio
async def test_handle_message_dm_sends_pairing_code(monkeypatch) -> None:
    channel = _DummyChannel({"allowFrom": []}, MessageBus())
    monkeypatch.setattr(
        "nanobot.channels.base.generate_code", lambda _ch, sid: "ABCD-EFGH"
    )

    await channel._handle_message(
        sender_id="stranger", chat_id="chat1", content="hello", is_dm=True
    )

    assert len(channel._sent) == 1
    msg = channel._sent[0]
    assert "ABCD-EFGH" in msg.content
    assert msg.metadata.get("_pairing_code") == "ABCD-EFGH"


@pytest.mark.asyncio
async def test_dm_during_transient_store_failure_keeps_approvals(
    tmp_path, monkeypatch
) -> None:
    """An unapproved DM while pairing.json is unreadable must not wipe approvals.

    The pairing store treated a transient OSError like corruption and returned
    an empty store; the DM pairing path then persisted that empty view,
    erasing every approved sender.
    """
    import builtins
    from pathlib import Path

    from nanobot.pairing import store

    path = tmp_path / "pairing.json"
    monkeypatch.setattr(store, "_store_path", lambda: path)
    code = store.generate_code("dummy", "friend")
    store.approve_code(code)

    channel = _DummyChannel({"allowFrom": []}, MessageBus())

    real_open = builtins.open

    def flaky_open(file, mode="r", *args, **kwargs):
        try:
            same = Path(file) == path
        except TypeError:
            same = False
        if same and "r" in mode and "+" not in mode:
            raise PermissionError(13, "temporarily locked", str(path))
        return real_open(file, mode, *args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", flaky_open)
        await channel._handle_message(
            sender_id="stranger", chat_id="chat1", content="hello", is_dm=True
        )

    assert channel._sent == []
    assert store.is_approved("dummy", "friend") is True


@pytest.mark.asyncio
async def test_handle_message_group_ignores_unknown() -> None:
    channel = _DummyChannel({"allowFrom": []}, MessageBus())

    await channel._handle_message(
        sender_id="stranger", chat_id="chat1", content="hello", is_dm=False
    )

    assert channel._sent == []


@pytest.mark.asyncio
async def test_handle_message_uses_authorization_id_without_changing_sender() -> None:
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["group@g.us"]}, bus)

    await channel._handle_message(
        sender_id="member-lid",
        authorization_id="group@g.us",
        chat_id="group@g.us",
        content="hello",
    )

    msg = await bus.consume_inbound()
    assert msg.sender_id == "member-lid"
    assert msg.chat_id == "group@g.us"


@pytest.mark.asyncio
async def test_handle_message_rejects_when_authorization_id_is_not_allowed() -> None:
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["member-lid"]}, bus)

    await channel._handle_message(
        sender_id="member-lid",
        authorization_id="other-group@g.us",
        chat_id="other-group@g.us",
        content="hello",
    )

    assert bus.inbound_size == 0


# -- noabot: owner identity & cross-channel session merge -----------------


def _stub_owner_ids(monkeypatch: pytest.MonkeyPatch, owner_ids: list[str] | None) -> None:
    """Canned owner list for BaseChannel._get_owner_ids' lazy config load.

    ``None`` makes the runtime config carry no identity section at all.
    """
    from nanobot.config import loader

    identity = None if owner_ids is None else SimpleNamespace(owner_ids=owner_ids)
    cfg = SimpleNamespace(agents=SimpleNamespace(identity=identity))
    monkeypatch.setattr(loader, "load_config", lambda *a, **k: cfg)


_GROUP_META = {"chat_type": "group", "is_group": True}


@pytest.mark.asyncio
async def test_owner_group_message_merges_into_shared_session(monkeypatch) -> None:
    _stub_owner_ids(monkeypatch, ["owner1"])
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "mergeOwnerInGroup": True}, bus)

    await channel._handle_message(
        sender_id="owner1", chat_id="g1", content="hi", metadata=dict(_GROUP_META)
    )

    msg = await bus.consume_inbound()
    assert msg.session_key_override == "owner:shared"
    assert msg.sender_id == "owner1"


@pytest.mark.asyncio
async def test_owner_merge_is_cross_channel(monkeypatch) -> None:
    """The same owner id reaching two different channels lands on one key."""

    class _OtherChannel(_DummyChannel):
        name = "other"

    _stub_owner_ids(monkeypatch, ["owner1"])
    bus = MessageBus()
    telegram_like = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)
    other_like = _OtherChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)

    await telegram_like._handle_message(
        sender_id="owner1", chat_id="g1", content="a", metadata=dict(_GROUP_META)
    )
    await other_like._handle_message(
        sender_id="owner1", chat_id="g2", content="b", metadata=dict(_GROUP_META)
    )

    first = await bus.consume_inbound()
    second = await bus.consume_inbound()
    assert first.session_key_override == "owner:shared"
    assert second.session_key_override == "owner:shared"


@pytest.mark.asyncio
async def test_owner_group_message_not_merged_without_channel_opt_in(monkeypatch) -> None:
    _stub_owner_ids(monkeypatch, ["owner1"])
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": False}, bus)

    await channel._handle_message(
        sender_id="owner1", chat_id="g1", content="hi", metadata=dict(_GROUP_META)
    )

    msg = await bus.consume_inbound()
    assert msg.session_key_override is None


@pytest.mark.asyncio
async def test_non_owner_group_message_is_not_merged(monkeypatch) -> None:
    _stub_owner_ids(monkeypatch, ["owner1"])
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)

    await channel._handle_message(
        sender_id="member", chat_id="g1", content="hi", metadata=dict(_GROUP_META)
    )

    msg = await bus.consume_inbound()
    assert msg.session_key_override is None


@pytest.mark.asyncio
async def test_owner_dm_does_not_merge_into_shared_session(monkeypatch) -> None:
    """DMs keep their per-channel session; only group context merges."""
    _stub_owner_ids(monkeypatch, ["owner1"])
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)

    await channel._handle_message(sender_id="owner1", chat_id="owner1", content="hi", is_dm=True)

    msg = await bus.consume_inbound()
    assert msg.session_key_override is None


@pytest.mark.asyncio
async def test_channel_prefixed_owner_id_matches(monkeypatch) -> None:
    _stub_owner_ids(monkeypatch, ["dummy:owner1"])
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)

    await channel._handle_message(
        sender_id="owner1", chat_id="g1", content="hi", metadata=dict(_GROUP_META)
    )

    msg = await bus.consume_inbound()
    assert msg.session_key_override == "owner:shared"


@pytest.mark.asyncio
async def test_explicit_session_key_wins_over_owner_merge(monkeypatch) -> None:
    _stub_owner_ids(monkeypatch, ["owner1"])
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)

    await channel._handle_message(
        sender_id="owner1",
        chat_id="g1",
        content="hi",
        metadata=dict(_GROUP_META),
        session_key="custom:key",
    )

    msg = await bus.consume_inbound()
    assert msg.session_key_override == "custom:key"


@pytest.mark.asyncio
async def test_owner_merge_disabled_when_runtime_config_unreadable(monkeypatch) -> None:
    from nanobot.config import loader

    def _boom(*_a, **_k):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(loader, "load_config", _boom)
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)

    await channel._handle_message(
        sender_id="owner1", chat_id="g1", content="hi", metadata=dict(_GROUP_META)
    )

    msg = await bus.consume_inbound()
    assert msg.session_key_override is None


@pytest.mark.asyncio
async def test_owner_merge_disabled_when_identity_section_missing(monkeypatch) -> None:
    _stub_owner_ids(monkeypatch, None)
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)

    await channel._handle_message(
        sender_id="owner1", chat_id="g1", content="hi", metadata=dict(_GROUP_META)
    )

    msg = await bus.consume_inbound()
    assert msg.session_key_override is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        None,  # fallback: chat_id != sender_id implies group
        {"conversation_type": "guild"},
        {"group_id": "g1"},
        {"is_group": True},
    ],
    ids=["chat-id-fallback", "conversation-type", "group-id", "is-group"],
)
async def test_group_context_detected_across_metadata_shapes(monkeypatch, metadata) -> None:
    _stub_owner_ids(monkeypatch, ["owner1"])
    bus = MessageBus()
    channel = _DummyChannel({"allowFrom": ["*"], "merge_owner_in_group": True}, bus)

    await channel._handle_message(
        sender_id="owner1", chat_id="g1", content="hi", metadata=metadata
    )

    msg = await bus.consume_inbound()
    assert msg.session_key_override == "owner:shared"

