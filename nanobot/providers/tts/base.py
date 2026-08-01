"""Platform-neutral TTS abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TTSAudio:
    """Normalized synthesized audio artifact."""

    path: Path
    audio_format: str
    sample_rate_hz: int | None = None
    duration_ms: int | None = None
    raw: dict[str, Any] | None = None


class BaseTTSProvider(ABC):
    """Abstract TTS provider interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier."""

    @abstractmethod
    async def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
        *,
        voice: str | None = None,
    ) -> TTSAudio:
        """Synthesize text to an audio file."""

