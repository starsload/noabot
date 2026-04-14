"""STT providers."""

from nanobot.providers.stt.base import BaseSTTProvider, STTResult
from nanobot.providers.stt.groq_stt import GroqSTTProvider

__all__ = [
    "BaseSTTProvider",
    "STTResult",
    "GroqSTTProvider",
]

