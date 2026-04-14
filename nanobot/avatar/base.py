"""Platform-neutral avatar runtime abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class AvatarIntent:
    """High-level avatar intent emitted by orchestrator."""

    expression: str | None = None
    motion: str | None = None
    speaking: bool | None = None
    intensity: float | None = None


class BaseAvatarRuntime(ABC):
    """Abstract avatar runtime (VTube Studio, Inochi, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Runtime identifier."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return whether runtime is currently connected."""

    @abstractmethod
    async def connect(self) -> None:
        """Connect runtime resources."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect runtime resources."""

    @abstractmethod
    async def apply_intent(self, intent: AvatarIntent) -> None:
        """Apply high-level intent to avatar runtime."""

