"""No-op avatar runtime for tests and headless environments."""

from __future__ import annotations

from nanobot.avatar.base import AvatarIntent, BaseAvatarRuntime


class NullAvatarRuntime(BaseAvatarRuntime):
    """A cross-platform no-op runtime used when avatar is disabled."""

    def __init__(self) -> None:
        self._connected = False
        self.last_intent: AvatarIntent | None = None

    @property
    def name(self) -> str:
        return "none"

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def apply_intent(self, intent: AvatarIntent) -> None:
        self.last_intent = intent

