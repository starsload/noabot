import json
from pathlib import Path

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.qq_personal import QQPersonalChannel, QQPersonalConfig


class _FakeResponse:
    def __init__(
        self,
        status: int = 200,
        json_body: dict | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self._json_body = json_body or {"status": "ok", "retcode": 0, "data": {}}
        self._body = body

    async def json(self):
        return self._json_body

    async def text(self) -> str:
        return json.dumps(self._json_body, ensure_ascii=False)

    async def read(self) -> bytes:
        return self._body

    def release(self) -> None:
        return None


class _FakeHttp:
    def __init__(
        self,
        *,
        post_responses: list[_FakeResponse] | None = None,
        get_responses: list[_FakeResponse] | None = None,
    ) -> None:
        self.post_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self._post_responses = list(post_responses or [])
        self._get_responses = list(get_responses or [])
        self.closed = False

    async def post(self, url: str, json=None, headers=None, **kwargs):
        self.post_calls.append({"url": url, "json": json, "headers": headers, "kwargs": kwargs})
        if self._post_responses:
            return self._post_responses.pop(0)
        return _FakeResponse()

    async def get(self, url: str, **kwargs):
        self.get_calls.append({"url": url, "kwargs": kwargs})
        if self._get_responses:
            return self._get_responses.pop(0)
        return _FakeResponse(body=b"")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_private_text_message_routes_to_sender_chat_id(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["user1"]),
        MessageBus(),
    )

    raw = json.dumps(
        {
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "message_id": 1001,
            "user_id": "user1",
            "message": [{"type": "text", "data": {"text": "hello"}}],
        },
        ensure_ascii=False,
    )

    await channel._handle_ws_message(raw)

    msg = await channel.bus.consume_inbound()
    assert msg.channel == "qq_personal"
    assert msg.sender_id == "user1"
    assert msg.chat_id == "user1"
    assert msg.content == "hello"


@pytest.mark.asyncio
async def test_ws_ignores_action_response_frames(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["*"]),
        MessageBus(),
    )

    await channel._handle_ws_message(json.dumps({"status": "ok", "retcode": 0, "data": {}}))

    assert channel.bus.inbound_size == 0


@pytest.mark.asyncio
async def test_send_private_text_uses_onebot_http_api_with_auth(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), access_token="secret-token", allow_from=["*"]),
        MessageBus(),
    )
    channel._http = _FakeHttp()

    await channel.send(
        OutboundMessage(
            channel="qq_personal",
            chat_id="123456",
            content="hello",
        )
    )

    assert len(channel._http.post_calls) == 1
    call = channel._http.post_calls[0]
    assert call["url"].endswith("/send_private_msg")
    assert call["json"] == {"user_id": 123456, "message": "hello"}
    assert call["headers"]["Authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_send_private_file_uses_file_segment(tmp_path) -> None:
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"%PDF-1.4")

    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["*"]),
        MessageBus(),
    )
    channel._http = _FakeHttp()

    await channel.send(
        OutboundMessage(
            channel="qq_personal",
            chat_id="123456",
            content="",
            media=[str(file_path)],
        )
    )

    assert len(channel._http.post_calls) == 1
    call = channel._http.post_calls[0]
    assert call["url"].endswith("/send_private_msg")
    assert call["json"]["user_id"] == 123456
    segment = call["json"]["message"][0]
    assert segment["type"] == "file"
    assert segment["data"]["file"] == str(file_path.resolve())
    assert segment["data"]["name"] == "report.pdf"


@pytest.mark.asyncio
async def test_receive_private_file_segment_downloads_and_attaches_media(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["user1"]),
        MessageBus(),
    )
    channel._http = _FakeHttp(
        post_responses=[
            _FakeResponse(
                json_body={
                    "status": "ok",
                    "retcode": 0,
                    "data": {"url": "https://files.example.com/report.xlsx"},
                }
            )
        ],
        get_responses=[_FakeResponse(body=b"sheet-bytes")],
    )

    raw = json.dumps(
        {
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "message_id": 1002,
            "user_id": "user1",
            "message": [
                {"type": "text", "data": {"text": "see attachment"}},
                {"type": "file", "data": {"file_id": "fid123", "name": "report.xlsx"}},
            ],
        },
        ensure_ascii=False,
    )

    await channel._handle_ws_message(raw)

    msg = await channel.bus.consume_inbound()
    assert msg.chat_id == "user1"
    assert msg.content.startswith("see attachment")
    assert len(msg.media) == 1
    saved_path = Path(msg.media[0])
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"sheet-bytes"
    assert "[file:" in msg.content
    assert len(msg.metadata["attachments"]) == 1
    assert msg.metadata["attachments"][0]["saved_path"] == str(saved_path)

    assert len(channel._http.post_calls) == 1
    action_call = channel._http.post_calls[0]
    assert action_call["url"].endswith("/get_private_file_url")
    assert action_call["json"] == {"user_id": "user1", "file_id": "fid123"}
    assert len(channel._http.get_calls) == 1
    assert channel._http.get_calls[0]["url"] == "https://files.example.com/report.xlsx"


@pytest.mark.asyncio
async def test_group_message_requires_mention_by_default(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["user1"]),
        MessageBus(),
    )

    raw = json.dumps(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 2001,
            "group_id": "group1",
            "user_id": "user1",
            "self_id": "bot123",
            "message": [{"type": "text", "data": {"text": "hello group"}}],
        },
        ensure_ascii=False,
    )

    await channel._handle_ws_message(raw)

    assert channel.bus.inbound_size == 0


@pytest.mark.asyncio
async def test_group_message_accepts_bot_at_and_routes_to_group_chat(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["user1"]),
        MessageBus(),
    )

    raw = json.dumps(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 2002,
            "group_id": "group1",
            "user_id": "user1",
            "self_id": "bot123",
            "sender": {"card": "Alice"},
            "message": [
                {"type": "at", "data": {"qq": "bot123"}},
                {"type": "text", "data": {"text": " hi bot"}},
            ],
        },
        ensure_ascii=False,
    )

    await channel._handle_ws_message(raw)

    msg = await channel.bus.consume_inbound()
    assert msg.sender_id == "user1"
    assert msg.chat_id == "group1"
    assert msg.metadata["chat_type"] == "group"
    assert msg.metadata["is_group"] is True
    assert msg.metadata["sender_name"] == "Alice"
    assert "hi bot" in msg.content


@pytest.mark.asyncio
async def test_group_policy_open_accepts_plain_group_message(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["user1"], group_policy="open"),
        MessageBus(),
    )

    raw = json.dumps(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 2003,
            "group_id": "group1",
            "user_id": "user1",
            "message": [{"type": "text", "data": {"text": "hello group"}}],
        },
        ensure_ascii=False,
    )

    await channel._handle_ws_message(raw)

    msg = await channel.bus.consume_inbound()
    assert msg.chat_id == "group1"
    assert msg.content == "hello group"


@pytest.mark.asyncio
async def test_send_group_text_uses_send_group_msg(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["*"]),
        MessageBus(),
    )
    channel._http = _FakeHttp()

    await channel.send(
        OutboundMessage(
            channel="qq_personal",
            chat_id="98765",
            content="hello group",
            metadata={"chat_type": "group"},
        )
    )

    assert len(channel._http.post_calls) == 1
    call = channel._http.post_calls[0]
    assert call["url"].endswith("/send_group_msg")
    assert call["json"] == {"group_id": 98765, "message": "hello group"}


@pytest.mark.asyncio
async def test_send_uses_group_cache_for_same_group_reply(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["*"]),
        MessageBus(),
    )
    channel._http = _FakeHttp()
    channel._chat_type_cache["98765"] = "group"

    await channel.send(
        OutboundMessage(
            channel="qq_personal",
            chat_id="98765",
            content="reply in same group",
        )
    )

    assert len(channel._http.post_calls) == 1
    call = channel._http.post_calls[0]
    assert call["url"].endswith("/send_group_msg")
    assert call["json"] == {"group_id": 98765, "message": "reply in same group"}


@pytest.mark.asyncio
async def test_send_group_file_uses_send_group_msg_file_segment(tmp_path) -> None:
    file_path = tmp_path / "group-report.pdf"
    file_path.write_bytes(b"%PDF-1.4")

    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["*"]),
        MessageBus(),
    )
    channel._http = _FakeHttp()

    await channel.send(
        OutboundMessage(
            channel="qq_personal",
            chat_id="98765",
            content="",
            media=[str(file_path)],
            metadata={"chat_type": "group"},
        )
    )

    assert len(channel._http.post_calls) == 1
    call = channel._http.post_calls[0]
    assert call["url"].endswith("/send_group_msg")
    assert call["json"]["group_id"] == 98765
    segment = call["json"]["message"][0]
    assert segment["type"] == "file"
    assert segment["data"]["file"] == str(file_path.resolve())
    assert segment["data"]["name"] == "group-report.pdf"


@pytest.mark.asyncio
async def test_receive_group_file_segment_downloads_and_attaches_media(tmp_path) -> None:
    channel = QQPersonalChannel(
        QQPersonalConfig(media_dir=str(tmp_path), allow_from=["user1"], group_policy="open"),
        MessageBus(),
    )
    channel._http = _FakeHttp(
        post_responses=[
            _FakeResponse(
                json_body={
                    "status": "ok",
                    "retcode": 0,
                    "data": {"url": "https://files.example.com/group-report.xlsx"},
                }
            )
        ],
        get_responses=[_FakeResponse(body=b"group-sheet")],
    )

    raw = json.dumps(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 2004,
            "group_id": "group1",
            "user_id": "user1",
            "message": [
                {"type": "text", "data": {"text": "group attachment"}},
                {"type": "file", "data": {"file_id": "gid123", "name": "group-report.xlsx"}},
            ],
        },
        ensure_ascii=False,
    )

    await channel._handle_ws_message(raw)

    msg = await channel.bus.consume_inbound()
    assert msg.chat_id == "group1"
    assert len(msg.media) == 1
    saved_path = Path(msg.media[0])
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"group-sheet"
    assert msg.metadata["chat_type"] == "group"
    assert msg.metadata["attachments"][0]["saved_path"] == str(saved_path)

    assert len(channel._http.post_calls) == 1
    action_call = channel._http.post_calls[0]
    assert action_call["url"].endswith("/get_group_file_url")
    assert action_call["json"] == {"group": "group1", "file_id": "gid123"}
    assert len(channel._http.get_calls) == 1
    assert channel._http.get_calls[0]["url"] == "https://files.example.com/group-report.xlsx"
