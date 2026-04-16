"""Base channel interface for chat platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the nanobot message bus.
    """

    name: str = "base"
    display_name: str = "Base"
    transcription_provider: str = "groq"
    transcription_api_key: str = ""

    def __init__(self, config: Any, bus: MessageBus):
        """
        Initialize the channel.

        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
        """
        self.config = config
        self.bus = bus
        self._running = False
        self._runtime_config: Any | None = None

    def set_runtime_config(self, runtime_config: Any) -> None:
        """Inject root runtime configuration when a channel needs global settings."""
        self._runtime_config = runtime_config

    async def transcribe_audio(self, file_path: str | Path) -> str:
        """Transcribe an audio file via Whisper (OpenAI or Groq). Returns empty string on failure."""
        if not self.transcription_api_key:
            return ""
        try:
            if self.transcription_provider == "openai":
                from nanobot.providers.transcription import OpenAITranscriptionProvider
                provider = OpenAITranscriptionProvider(api_key=self.transcription_api_key)
            else:
                from nanobot.providers.transcription import GroqTranscriptionProvider
                provider = GroqTranscriptionProvider(api_key=self.transcription_api_key)
            return await provider.transcribe(file_path)
        except Exception as e:
            logger.warning("{}: audio transcription failed: {}", self.name, e)
            return ""

    async def login(self, force: bool = False) -> bool:
        """
        Perform channel-specific interactive login (e.g. QR code scan).

        Args:
            force: If True, ignore existing credentials and force re-authentication.

        Returns True if already authenticated or login succeeds.
        Override in subclasses that support interactive login.
        """
        return True

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Args:
            msg: The message to send.

        Implementations should raise on delivery failure so the channel manager
        can apply any retry policy in one place.
        """
        pass

    async def send_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None) -> None:
        """Deliver a streaming text chunk.

        Override in subclasses to enable streaming. Implementations should
        raise on delivery failure so the channel manager can retry.

        Streaming contract: ``_stream_delta`` is a chunk, ``_stream_end`` ends
        the current segment, and stateful implementations must key buffers by
        ``_stream_id`` rather than only by ``chat_id``.
        """
        pass

    @property
    def supports_streaming(self) -> bool:
        """True when config enables streaming AND this subclass implements send_delta."""
        cfg = self.config
        streaming = cfg.get("streaming", False) if isinstance(cfg, dict) else getattr(cfg, "streaming", False)
        return bool(streaming) and type(self).send_delta is not BaseChannel.send_delta

    def is_allowed(self, sender_id: str) -> bool:
        """Check if *sender_id* is permitted.  Empty list → deny all; ``"*"`` → allow all."""
        if isinstance(self.config, dict):
            if "allow_from" in self.config:
                allow_list = self.config.get("allow_from")
            else:
                allow_list = self.config.get("allowFrom", [])
        else:
            allow_list = getattr(self.config, "allow_from", [])
        if not allow_list:
            logger.warning("{}: allow_from is empty — all access denied", self.name)
            return False
        if "*" in allow_list:
            return True
        return str(sender_id) in allow_list

    def _get_owner_ids(self) -> set[str]:
        """Get owner_ids from runtime config (agents.identity.owner_ids)."""
        if self._runtime_config is None:
            return set()
        identity = getattr(self._runtime_config, "agents", None)
        if identity is None:
            return set()
        identity_cfg = getattr(identity, "identity", None)
        if identity_cfg is None:
            return set()
        return set(identity_cfg.owner_ids or [])

    def _get_merge_owner_in_group(self) -> bool:
        """Get merge_owner_in_group config from this channel's config."""
        cfg = self.config
        if isinstance(cfg, dict):
            # Check both snake_case and camelCase
            return cfg.get("merge_owner_in_group", cfg.get("mergeOwnerInGroup", False))
        # For pydantic models, check both formats
        return getattr(cfg, "merge_owner_in_group", getattr(cfg, "mergeOwnerInGroup", False))

    def _is_group_context(self, chat_id: str, sender_id: str, metadata: dict[str, Any] | None) -> bool:
        """Determine if the current context is a group conversation (not a DM).

        Uses metadata hints (conversation_type, guild_id, group_id) or
        infers from chat_id != sender_id for platforms where DM chat_id equals sender_id.
        """
        if metadata:
            # Check explicit conversation_type marker
            conv_type = metadata.get("conversation_type", "").lower()
            if conv_type in {"group", "guild", "channel", "room", "thread"}:
                return True
            # Check platform-specific group identifiers
            if any(metadata.get(k) for k in ("guild_id", "group_id", "room_id", "team_id")):
                return True
        # Fallback: for many platforms, DM's chat_id equals sender_id
        return chat_id != sender_id

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """
        Handle an incoming message from the chat platform.

        This method checks permissions and forwards to the bus.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: Message text content.
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
            session_key: Optional session key override (e.g. thread-scoped sessions).
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id, self.name,
            )
            return

        # Auto-merge owner's group messages into shared owner session if configured
        if session_key is None:
            owner_ids = self._get_owner_ids()
            if owner_ids:
                # Build candidate set similar to SessionManager.is_owner_session
                candidates = {str(sender_id), f"{self.name}:{sender_id}"}
                is_owner = any(c in owner_ids for c in candidates)
                is_group = self._is_group_context(chat_id, sender_id, metadata)
                merge_enabled = self._get_merge_owner_in_group()
                logger.info(
                    "{}: owner merge check - sender={}, owner_ids={}, candidates={}, is_owner={}, is_group={}, merge_enabled={}",
                    self.name, sender_id, owner_ids, candidates, is_owner, is_group, merge_enabled
                )
                if is_owner and is_group and merge_enabled:
                    session_key = "owner:shared"
                    logger.info("{}: merging owner {} into shared session", self.name, sender_id)

        meta = metadata or {}
        if self.supports_streaming:
            meta = {**meta, "_wants_stream": True}

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=meta,
            session_key_override=session_key,
        )

        await self.bus.publish_inbound(msg)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """Return default config for onboard. Override in plugins to auto-populate config.json."""
        return {"enabled": False}

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
