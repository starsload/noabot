"""Cross-platform desktop voice/avatar orchestrator."""

from __future__ import annotations

import asyncio
from pathlib import Path

from nanobot.avatar.base import AvatarIntent, BaseAvatarRuntime
from nanobot.avatar.state_machine import AvatarTurnState, AvatarTurnStateMachine
from nanobot.providers.stt.base import BaseSTTProvider, STTResult
from nanobot.providers.tts.base import BaseTTSProvider, TTSAudio
from nanobot.voice.audio_io import BaseAudioIO, NullAudioIO


class DesktopVoiceAvatarOrchestrator:
    """Coordinates STT, TTS, and avatar runtime in a platform-neutral way."""

    def __init__(
        self,
        *,
        stt: BaseSTTProvider,
        tts: BaseTTSProvider,
        avatar: BaseAvatarRuntime,
        audio_io: BaseAudioIO | None = None,
    ) -> None:
        self.stt = stt
        self.tts = tts
        self.avatar = avatar
        self.audio_io = audio_io or NullAudioIO()
        self.state_machine = AvatarTurnStateMachine()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> AvatarTurnState:
        return self.state_machine.state

    async def start(self) -> None:
        if not self.avatar.is_connected:
            await self.avatar.connect()

    async def shutdown(self) -> None:
        if self.avatar.is_connected:
            await self.avatar.disconnect()
        self.state_machine.reset()

    async def transcribe_audio_file(
        self,
        file_path: str | Path,
        *,
        language: str | None = None,
    ) -> STTResult:
        self.state_machine.start_listening()
        result = await self.stt.transcribe_file(file_path, language=language)
        self.state_machine.finish_listening()
        return result

    async def speak_text(
        self,
        text: str,
        output_path: str | Path,
        *,
        voice: str | None = None,
        expression: str | None = None,
        motion: str | None = None,
    ) -> TTSAudio:
        async with self._lock:
            self.state_machine.start_speaking()
            await self.avatar.apply_intent(
                AvatarIntent(expression=expression, motion=motion, speaking=True)
            )
            try:
                audio = await self.tts.synthesize_to_file(text, output_path, voice=voice)
                await self.audio_io.play_file(audio.path)
            except Exception:
                self.state_machine.finish_speaking()
                await self.avatar.apply_intent(AvatarIntent(speaking=False))
                raise
            await self.avatar.apply_intent(AvatarIntent(speaking=False))
            self.state_machine.finish_speaking()
            return audio

    async def interrupt(self) -> None:
        self.state_machine.interrupt()
        await self.audio_io.stop_playback()
        await self.avatar.apply_intent(AvatarIntent(speaking=False))
