"""TTS providers."""

from nanobot.providers.tts.base import BaseTTSProvider, TTSAudio
from nanobot.providers.tts.edge_tts import EdgeTTSProvider

__all__ = [
    "BaseTTSProvider",
    "TTSAudio",
    "EdgeTTSProvider",
]

