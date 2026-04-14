"""Platform-neutral STT abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class STTResult:
    """Normalized speech-to-text result."""

    text: str
    language: str | None = None
    confidence: float | None = None
    raw: dict[str, Any] | None = None


class BaseSTTProvider(ABC):
    """Abstract STT provider interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier."""

    @abstractmethod
    async def transcribe_file(
        self,
        file_path: str | Path,
        *,
        language: str | None = None,
    ) -> STTResult:
        """Transcribe an audio file to text."""

