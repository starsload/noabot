from pathlib import Path

import pytest

from nanobot.avatar.base import AvatarIntent, BaseAvatarRuntime
from nanobot.avatar.orchestrator import DesktopVoiceAvatarOrchestrator
from nanobot.avatar.state_machine import AvatarTurnState
from nanobot.providers.stt.base import BaseSTTProvider, STTResult
from nanobot.providers.tts.base import BaseTTSProvider, TTSAudio
from nanobot.voice.audio_io import BaseAudioIO


class _FakeSTT(BaseSTTProvider):
    @property
    def name(self) -> str:
        return "fake_stt"

    async def transcribe_file(
        self,
        file_path: str | Path,
        *,
        language: str | None = None,
    ) -> STTResult:
        _ = language
        return STTResult(text=f"from:{Path(file_path).name}")


class _FakeTTS(BaseTTSProvider):
    @property
    def name(self) -> str:
        return "fake_tts"

    async def synthesize_to_file(
        self,
        text: str,
        output_path: str | Path,
        *,
        voice: str | None = None,
    ) -> TTSAudio:
        _ = text, voice
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return TTSAudio(path=path, audio_format="mp3")


class _FakeAvatar(BaseAvatarRuntime):
    def __init__(self) -> None:
        self._connected = False
        self.intents: list[AvatarIntent] = []

    @property
    def name(self) -> str:
        return "fake_avatar"

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def apply_intent(self, intent: AvatarIntent) -> None:
        self.intents.append(intent)


class _FakeAudioIO(BaseAudioIO):
    def __init__(self) -> None:
        self.played: list[Path] = []
        self.stop_calls = 0

    @property
    def name(self) -> str:
        return "fake_audio"

    async def play_file(self, file_path: str | Path) -> None:
        self.played.append(Path(file_path).resolve())

    async def stop_playback(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_orchestrator_transcribe_and_speak_flow(tmp_path: Path) -> None:
    avatar = _FakeAvatar()
    audio = _FakeAudioIO()
    orchestrator = DesktopVoiceAvatarOrchestrator(
        stt=_FakeSTT(),
        tts=_FakeTTS(),
        avatar=avatar,
        audio_io=audio,
    )

    await orchestrator.start()
    assert avatar.is_connected is True

    result = await orchestrator.transcribe_audio_file("sample.wav")
    assert result.text == "from:sample.wav"
    assert orchestrator.state == AvatarTurnState.THINKING

    tts_audio = await orchestrator.speak_text(
        "hello",
        tmp_path / "speech.mp3",
        expression="smile",
        motion="wave",
    )
    assert tts_audio.path.exists()
    assert orchestrator.state == AvatarTurnState.IDLE
    assert tts_audio.path.resolve() in audio.played
    assert avatar.intents[0] == AvatarIntent(expression="smile", motion="wave", speaking=True)
    assert avatar.intents[-1] == AvatarIntent(speaking=False)


@pytest.mark.asyncio
async def test_orchestrator_interrupt_sets_state_and_silences_avatar() -> None:
    avatar = _FakeAvatar()
    audio = _FakeAudioIO()
    orchestrator = DesktopVoiceAvatarOrchestrator(
        stt=_FakeSTT(),
        tts=_FakeTTS(),
        avatar=avatar,
        audio_io=audio,
    )

    await orchestrator.interrupt()
    assert orchestrator.state == AvatarTurnState.INTERRUPTED
    assert audio.stop_calls == 1
    assert avatar.intents[-1] == AvatarIntent(speaking=False)
