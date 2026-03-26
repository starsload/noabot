"""QQ personal channel implementation using botpy SDK."""

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Base

try:
    import botpy
    from botpy.message import C2CMessage

    QQ_PERSONAL_AVAILABLE = True
except ImportError:
    QQ_PERSONAL_AVAILABLE = False
    botpy = None
    C2CMessage = None

if TYPE_CHECKING:
    from botpy.message import C2CMessage


def _make_bot_class(channel: "QQPersonalChannel") -> "type[botpy.Client]":
    """Create a botpy Client subclass bound to the given channel."""
    intents = botpy.Intents(public_messages=True, direct_message=True)

    class _Bot(botpy.Client):
        def __init__(self):
            # Disable botpy's file log. nanobot uses loguru and can run on read-only FS.
            super().__init__(intents=intents, ext_handlers=False)

        async def on_ready(self):
            logger.info("QQ personal bot ready: {}", self.robot.name)

        async def on_c2c_message_create(self, message: "C2CMessage"):
            await channel._on_message(message)

        async def on_direct_message_create(self, message):
            await channel._on_message(message)

    return _Bot


class QQPersonalConfig(Base):
    """QQ personal channel configuration using botpy SDK."""

    enabled: bool = False
    app_id: str = ""
    secret: str = ""
    allow_from: list[str] = Field(default_factory=list)
    msg_format: Literal["plain", "markdown"] = "plain"


class QQPersonalChannel(BaseChannel):
    """QQ personal channel using botpy SDK with a WebSocket connection."""

    name = "qq_personal"
    display_name = "QQ Personal"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return QQPersonalConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = QQPersonalConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: QQPersonalConfig = config
        self._client: "botpy.Client | None" = None
        self._processed_ids: deque[str] = deque(maxlen=1000)
        self._msg_seq = 1

    async def start(self) -> None:
        """Start the QQ personal bot."""
        if not QQ_PERSONAL_AVAILABLE:
            logger.error("QQ SDK not installed. Run: pip install qq-botpy")
            return

        if not self.config.app_id or not self.config.secret:
            logger.error("QQ personal app_id and secret not configured")
            return

        self._running = True
        bot_class = _make_bot_class(self)
        self._client = bot_class()
        logger.info("QQ personal bot started (C2C private message)")
        await self._run_bot()

    async def _run_bot(self) -> None:
        """Run the bot connection with auto-reconnect."""
        while self._running:
            try:
                await self._client.start(appid=self.config.app_id, secret=self.config.secret)
            except Exception as exc:
                logger.warning("QQ personal bot error: {}", exc)
            if self._running:
                logger.info("Reconnecting QQ personal bot in 5 seconds...")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the QQ personal bot."""
        self._running = False
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
        logger.info("QQ personal bot stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Send a private message through QQ."""
        if not self._client:
            logger.warning("QQ personal client not initialized")
            return

        try:
            self._msg_seq += 1
            use_markdown = self.config.msg_format == "markdown"
            payload: dict[str, Any] = {
                "openid": msg.chat_id,
                "msg_type": 2 if use_markdown else 0,
                "msg_id": msg.metadata.get("message_id"),
                "msg_seq": self._msg_seq,
            }
            if use_markdown:
                payload["markdown"] = {"content": msg.content}
            else:
                payload["content"] = msg.content

            await self._client.api.post_c2c_message(**payload)
        except Exception as exc:
            logger.error("Error sending QQ personal message: {}", exc)

    async def _on_message(self, data: "C2CMessage") -> None:
        """Handle an incoming QQ private message."""
        try:
            if data.id in self._processed_ids:
                return
            self._processed_ids.append(data.id)

            content = (data.content or "").strip()
            if not content:
                return

            author = getattr(data, "author", None)
            user_id = str(
                getattr(author, "id", None)
                or getattr(author, "user_openid", None)
                or "unknown"
            )

            await self._handle_message(
                sender_id=user_id,
                chat_id=user_id,
                content=content,
                metadata={"message_id": data.id},
            )
        except Exception:
            logger.exception("Error handling QQ personal message")
