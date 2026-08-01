"""Groq STT adapter using existing transcription implementation."""

from __future__ import annotations

from pathlib import Path

from nanobot.providers.stt.base import BaseSTTProvider, STTResult
from nanobot.providers.transcription import GroqTranscriptionProvider


class GroqSTTProvider(BaseSTTProvider):
    """STT adapter backed by Groq Whisper."""

    def __init__(self, api_key: str | None = None):
        self._provider = GroqTranscriptionProvider(api_key=api_key)

    @property
    def name(self) -> str:
        return "groq"

    async def transcribe_file(
        self,
        file_path: str | Path,
        *,
        language: str | None = None,
    ) -> STTResult:
        # Groq's current endpoint auto-detects language; keep language for future parity.
        _ = language
        text = await self._provider.transcribe(file_path)
        return STTResult(text=text or "", raw={"provider": self.name})

