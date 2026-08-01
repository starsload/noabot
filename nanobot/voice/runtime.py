"""Desktop voice turn runner for STT -> agent -> TTS -> playback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from nanobot.avatar.orchestrator import DesktopVoiceAvatarOrchestrator
from nanobot.voice.capture import BaseAudioCapture


class _AgentLike(Protocol):
    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        **kwargs,
    ) -> str: ...


@dataclass(slots=True)
class VoiceTurnResult:
    transcript: str
    response: str
    synthesized_audio: Path
    input_audio: Path


def infer_expression_from_text(text: str) -> str | None:
    """Very small heuristic mapper; can be replaced by richer emotion mapper later."""
    lower = text.lower()
    if any(token in lower for token in ("谢谢", "开心", "高兴", "太好了", "great", "nice", "happy")):
        return "smile"
    if any(token in lower for token in ("抱歉", "对不起", "sorry")):
        return "sad"
    return None


class DesktopVoiceTurnRunner:
    """Run one desktop voice interaction turn."""

    def __init__(
        self,
        *,
        orchestrator: DesktopVoiceAvatarOrchestrator,
        agent: _AgentLike,
        output_dir: str | Path,
        session_key: str = "desktop:local",
        channel: str = "cli",
        chat_id: str = "desktop-local",
    ) -> None:
        self.orchestrator = orchestrator
        self.agent = agent
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session_key = session_key
        self.channel = channel
        self.chat_id = chat_id

    def _new_output_path(self) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return self.output_dir / f"reply-{stamp}.mp3"

    async def run_turn_from_audio_file(
        self,
        input_audio: str | Path,
        *,
        language: str | None = None,
        voice: str | None = None,
    ) -> VoiceTurnResult:
        input_path = Path(input_audio).expanduser().resolve()
        stt = await self.orchestrator.transcribe_audio_file(input_path, language=language)
        transcript = (stt.text or "").strip()
        if not transcript:
            response = "I could not hear clear speech in the input audio."
        else:
            response = await self.agent.process_direct(
                transcript,
                session_key=self.session_key,
                channel=self.channel,
                chat_id=self.chat_id,
            )
        out_path = self._new_output_path()
        expression = infer_expression_from_text(response)
        tts_audio = await self.orchestrator.speak_text(
            response,
            out_path,
            voice=voice,
            expression=expression,
        )
        return VoiceTurnResult(
            transcript=transcript,
            response=response,
            synthesized_audio=tts_audio.path,
            input_audio=input_path,
        )

    async def run_turn_from_capture(
        self,
        capture: BaseAudioCapture,
        *,
        duration_s: float = 5.0,
        language: str | None = None,
        voice: str | None = None,
    ) -> VoiceTurnResult:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        capture_path = self.output_dir / f"capture-{stamp}.wav"
        input_audio = await capture.capture_to_file(capture_path, duration_s=duration_s)
        return await self.run_turn_from_audio_file(input_audio, language=language, voice=voice)
