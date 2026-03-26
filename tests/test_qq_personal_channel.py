from types import SimpleNamespace

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.qq_personal import QQPersonalChannel
from nanobot.channels.qq_personal import QQPersonalConfig


class _FakeApi:
    def __init__(self) -> None:
        self.c2c_calls: list[dict] = []

    async def post_c2c_message(self, **kwargs) -> None:
        self.c2c_calls.append(kwargs)


class _FakeClient:
    def __init__(self) -> None:
        self.api = _FakeApi()


@pytest.mark.asyncio
async def test_on_message_routes_to_sender_chat_id() -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(app_id="app", secret="secret", allow_from=["user1"]),
        MessageBus(),
    )

    data = SimpleNamespace(
        id="msg1",
        content="hello",
        author=SimpleNamespace(user_openid="user1"),
    )

    await channel._on_message(data)

    msg = await channel.bus.consume_inbound()
    assert msg.channel == "qq_personal"
    assert msg.sender_id == "user1"
    assert msg.chat_id == "user1"


@pytest.mark.asyncio
async def test_on_message_deduplicates_by_message_id() -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(app_id="app", secret="secret", allow_from=["user1"]),
        MessageBus(),
    )

    data = SimpleNamespace(
        id="msg1",
        content="hello",
        author=SimpleNamespace(user_openid="user1"),
    )

    await channel._on_message(data)
    await channel._on_message(data)

    msg = await channel.bus.consume_inbound()
    assert msg.chat_id == "user1"
    assert channel.bus.inbound_size == 0


@pytest.mark.asyncio
async def test_send_c2c_message_uses_plain_text_payload_with_msg_seq() -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(app_id="app", secret="secret", allow_from=["*"]),
        MessageBus(),
    )
    channel._client = _FakeClient()

    await channel.send(
        OutboundMessage(
            channel="qq_personal",
            chat_id="user123",
            content="hello",
            metadata={"message_id": "msg1"},
        )
    )

    assert channel._client.api.c2c_calls == [
        {
            "openid": "user123",
            "msg_type": 0,
            "content": "hello",
            "msg_id": "msg1",
            "msg_seq": 2,
        }
    ]


@pytest.mark.asyncio
async def test_send_c2c_message_uses_markdown_when_configured() -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(app_id="app", secret="secret", allow_from=["*"], msg_format="markdown"),
        MessageBus(),
    )
    channel._client = _FakeClient()

    await channel.send(
        OutboundMessage(
            channel="qq_personal",
            chat_id="user123",
            content="**hello**",
            metadata={"message_id": "msg1"},
        )
    )

    assert channel._client.api.c2c_calls == [
        {
            "openid": "user123",
            "msg_type": 2,
            "markdown": {"content": "**hello**"},
            "msg_id": "msg1",
            "msg_seq": 2,
        }
    ]
