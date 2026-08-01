"""Desktop voice channel integrated with the standard gateway/channel manager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import Field

from nanobot.avatar import (
    BaseAvatarRuntime,
    DesktopVoiceAvatarOrchestrator,
    NullAvatarRuntime,
    VTubeStudioRuntime,
)
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Base, Config
from nanobot.providers.stt import GroqSTTProvider
from nanobot.providers.tts import EdgeTTSProvider
from nanobot.voice import (
    CommandAudioCapture,
    CommandAudioIO,
    NullAudioIO,
    SoundDeviceAudioCapture,
    SoundDeviceAudioIO,
    parse_device_selector,
)
from nanobot.voice.runtime import infer_expression_from_text


class DesktopVoiceConfig(Base):
    """Configuration for the local desktop voice channel."""

    enabled: bool = False
    allow_from: list[str] = Field(default_factory=lambda: ["desktop_user"])
    listen_input: bool = True
    sender_id: str = "desktop_user"
    chat_id: str = "desktop_local"
    session_key: str = "desktop:local"
    duration_s: float | None = Field(default=None, ge=0.1, le=600.0)
    native_capture: bool = False
    capture_cmd_part: list[str] = Field(default_factory=list)
    playback: bool = False
    avatar: bool = False
    mirror_from_channels: list[str] = Field(default_factory=list)
    stop_on_empty: bool = False
    max_turns: int = Field(default=0, ge=0)
    output_dir: str = ""


class DesktopVoiceChannel(BaseChannel):
    """Built-in channel that captures local audio and routes it through gateway."""

    name = "desktop_voice"
    display_name = "Desktop Voice"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return DesktopVoiceConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = DesktopVoiceConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: DesktopVoiceConfig = config
        self.runtime_config: Config | None = None
        self._capture: Any | None = None
        self._orchestrator: DesktopVoiceAvatarOrchestrator | None = None
        self._turn_done = asyncio.Event()
        self._turn_done.set()
        self._turn_count = 0

    def set_runtime_config(self, runtime_config: Any) -> None:
        super().set_runtime_config(runtime_config)
        if isinstance(runtime_config, Config):
            self.runtime_config = runtime_config

    def _require_runtime_config(self) -> Config:
        if self.runtime_config is None:
            raise RuntimeError("DesktopVoiceChannel requires injected runtime config")
        return self.runtime_config

    def _resolve_output_dir(self) -> Path:
        runtime_config = self._require_runtime_config()
        if self.config.output_dir:
            return Path(self.config.output_dir).expanduser().resolve()
        return (runtime_config.workspace_path / "runtime" / "desktop_voice_channel").resolve()

    def _make_capture(self):
        runtime_config = self._require_runtime_config()
        if self.config.native_capture or runtime_config.voice.capture.backend == "sounddevice":
            return SoundDeviceAudioCapture(
                sample_rate_hz=runtime_config.voice.capture.sample_rate_hz,
                channels=runtime_config.voice.capture.channels,
                input_device=parse_device_selector(runtime_config.voice.input_device),
            )

        template = list(self.config.capture_cmd_part or runtime_config.voice.capture.command_template)
        if not template:
            raise RuntimeError("desktop_voice requires capture command template or native capture")
        return CommandAudioCapture(template)

    def _make_audio_io(self):
        runtime_config = self._require_runtime_config()
        if not self.config.playback:
            return NullAudioIO()
        if (runtime_config.voice.playback.backend or "").strip().lower() == "sounddevice":
            return SoundDeviceAudioIO(
                output_device=parse_device_selector(runtime_config.voice.output_device),
            )
        return CommandAudioIO(play_command=list(runtime_config.voice.playback.command_template))

    def _make_avatar_runtime(self):
        runtime_config = self._require_runtime_config()
        if not self.config.avatar:
            return NullAvatarRuntime()
        return VTubeStudioRuntime(
            host=runtime_config.avatar.host,
            port=runtime_config.avatar.port,
            plugin_name=runtime_config.avatar.plugin_name,
            plugin_developer=runtime_config.avatar.plugin_developer,
            token_path=runtime_config.avatar.token_path,
            speaking_parameter=runtime_config.avatar.speaking_parameter,
            speaking_value_on=runtime_config.avatar.speaking_value_on,
            speaking_value_off=runtime_config.avatar.speaking_value_off,
            expression_hotkeys=runtime_config.avatar.expression_hotkeys,
            motion_hotkeys=runtime_config.avatar.motion_hotkeys,
        )

    def _make_orchestrator(
        self,
        avatar_runtime: BaseAvatarRuntime | None = None,
    ) -> DesktopVoiceAvatarOrchestrator:
        runtime_config = self._require_runtime_config()
        stt = GroqSTTProvider(api_key=runtime_config.providers.groq.api_key or None)
        tts = EdgeTTSProvider(
            default_voice=runtime_config.voice.tts.voice,
            rate=runtime_config.voice.tts.rate,
            volume=runtime_config.voice.tts.volume,
            pitch=runtime_config.voice.tts.pitch,
        )
        return DesktopVoiceAvatarOrchestrator(
            stt=stt,
            tts=tts,
            avatar=avatar_runtime or self._make_avatar_runtime(),
            audio_io=self._make_audio_io(),
        )

    async def start(self) -> None:
        self._require_runtime_config()
        self._running = True
        self._turn_done.set()
        self._turn_count = 0
        self._orchestrator = self._make_orchestrator()
        try:
            await self._orchestrator.start()
        except Exception as exc:
            if not self.config.avatar:
                raise
            logger.warning(
                "desktop_voice: avatar runtime failed to start ({}). "
                "Falling back to output without avatar.",
                exc,
            )
            self._orchestrator = self._make_orchestrator(avatar_runtime=NullAvatarRuntime())
            await self._orchestrator.start()

        output_dir = self._resolve_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            if not self.config.listen_input:
                while self._running:
                    await asyncio.sleep(0.2)
                return

            self._capture = self._make_capture()
            while self._running:
                if self.config.max_turns and self._turn_count >= self.config.max_turns:
                    break

                await self._turn_done.wait()
                if not self._running:
                    break

                self._turn_done.clear()
                stamp = f"{self._turn_count + 1:04d}"
                capture_path = output_dir / f"capture-{stamp}.wav"
                captured = await self._capture.capture_to_file(
                    capture_path,
                    duration_s=self.config.duration_s or self._require_runtime_config().voice.capture.duration_s,
                )
                stt_result = await self._orchestrator.transcribe_audio_file(
                    captured,
                    language=self._require_runtime_config().voice.stt.language,
                )
                transcript = (stt_result.text or "").strip()
                if not transcript:
                    self._turn_done.set()
                    if self.config.stop_on_empty:
                        break
                    continue

                self._turn_count += 1
                await self._handle_message(
                    sender_id=self.config.sender_id,
                    chat_id=self.config.chat_id,
                    content=transcript,
                    session_key=self.config.session_key,
                )
        finally:
            self._running = False
            self._turn_done.set()
            if self._orchestrator is not None:
                await self._orchestrator.shutdown()

    async def stop(self) -> None:
        self._running = False
        self._turn_done.set()
        if self._orchestrator is not None:
            await self._orchestrator.interrupt()

    async def send(self, msg: OutboundMessage) -> None:
        try:
            if self._orchestrator is None:
                return
            if msg.chat_id != self.config.chat_id:
                return
            content = (msg.content or "").strip()
            if not content or content == "[empty message]":
                return

            output_dir = self._resolve_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"reply-{self._turn_count:04d}.mp3"
            expression = infer_expression_from_text(content)
            await self._orchestrator.speak_text(
                content,
                output_path,
                voice=self._require_runtime_config().voice.tts.voice,
                expression=expression,
            )
        finally:
            self._turn_done.set()
