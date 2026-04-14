"""Edge TTS provider adapter."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Callable

from nanobot.providers.tts.base import BaseTTSProvider, TTSAudio


class EdgeTTSProvider(BaseTTSProvider):
    """Text-to-speech via edge-tts package."""

    def __init__(
        self,
        *,
        default_voice: str = "zh-CN-XiaoxiaoNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        module_loader: Callable[[str], ModuleType] = import_module,
    ) -> None:
        self.default_voice = default_voice
        self.rate = rate
        self.volume = volume
        self.pitch = pitch
        try:
            self._edge_tts = module_loader("edge_tts")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "edge-tts is required for EdgeTTSProvider. Install with: pip install edge-tts"
            ) from exc

    @property
    def name(self) -> str:
        return "edge_tts"

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
        *,
        voice: str | None = None,
    ) -> TTSAudio:
        path = Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        chosen_voice = voice or self.default_voice
        communicate = self._edge_tts.Communicate(
            text=text,
            voice=chosen_voice,
            rate=self.rate,
            volume=self.volume,
            pitch=self.pitch,
        )
        await communicate.save(str(path))

        audio_format = path.suffix.lstrip(".").lower() or "mp3"
        return TTSAudio(
            path=path,
            audio_format=audio_format,
            raw={"provider": self.name, "voice": chosen_voice},
        )

