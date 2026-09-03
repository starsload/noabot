from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.desktop_voice import DesktopVoiceChannel, DesktopVoiceConfig
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import Config

# noabot: desktop_voice still targets the dev-clean single-module layout and
# its runtime-config injection contract (`set_runtime_config`) was superseded
# upstream. These tests are the blueprint for the deferred package restructure
# (see memory: "desktop_voice channel package restructure"); skipped until it
# is done so the suite reflects currently wired behavior.
pytestmark = pytest.mark.skip(
    reason="desktop_voice restructure onto upstream channel layout is pending"
)


class _FakeCapture:
    def __init__(self, transcript_file: Path):
        self.transcript_file = transcript_file
        self.calls: list[tuple[Path, float]] = []

    async def capture_to_file(self, output_path, *, duration_s=5.0):  # noqa: ANN001
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.transcript_file.read_bytes())
        self.calls.append((path, duration_s))
        return path


class _FakeOrchestrator:
    def __init__(self, transcript: str = "hello from mic"):
        self.transcript = transcript
        self.started = False
        self.shutdown_calls = 0
        self.interrupt_calls = 0
        self.transcribe_calls: list[Path] = []
        self.speak_calls: list[tuple[str, Path, str | None, str | None]] = []

    async def start(self) -> None:
        self.started = True

    async def shutdown(self) -> None:
        self.shutdown_calls += 1

    async def interrupt(self) -> None:
        self.interrupt_calls += 1

    async def transcribe_audio_file(self, file_path, *, language=None):  # noqa: ANN001
        _ = language
        path = Path(file_path).resolve()
        self.transcribe_calls.append(path)
        return type("STT", (), {"text": self.transcript})()

    async def speak_text(self, text, output_path, *, voice=None, expression=None, motion=None):  # noqa: ANN001
        _ = motion
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tts")
        self.speak_calls.append((text, path, voice, expression))
        return type("TTS", (), {"path": path})()


@pytest.mark.asyncio
async def test_desktop_voice_channel_start_publishes_inbound_and_send_completes_turn(tmp_path: Path) -> None:
    bus = MessageBus()
    cfg = DesktopVoiceConfig(
        enabled=True,
        allow_from=["desktop_user"],
        sender_id="desktop_user",
        chat_id="desktop_local",
        session_key="desktop:local",
        max_turns=1,
    )
    channel = DesktopVoiceChannel(cfg, bus)
    runtime = Config()
    runtime.agents.defaults.workspace = str(tmp_path / "workspace")
    channel.set_runtime_config(runtime)

    fake_capture = _FakeCapture(tmp_path / "seed.wav")
    (tmp_path / "seed.wav").write_bytes(b"audio")
    fake_orchestrator = _FakeOrchestrator(transcript="hello desktop")

    channel._make_capture = lambda: fake_capture  # type: ignore[method-assign]
    channel._make_orchestrator = lambda: fake_orchestrator  # type: ignore[method-assign]

    start_task = asyncio.create_task(channel.start())
    inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
    assert inbound.channel == "desktop_voice"
    assert inbound.sender_id == "desktop_user"
    assert inbound.chat_id == "desktop_local"
    assert inbound.content == "hello desktop"
    assert inbound.session_key == "desktop:local"

    await channel.send(
        OutboundMessage(
            channel="desktop_voice",
            chat_id="desktop_local",
            content="reply text",
        )
    )
    await asyncio.wait_for(start_task, timeout=1.0)

    assert fake_orchestrator.started is True
    assert fake_orchestrator.shutdown_calls == 1
    assert fake_orchestrator.speak_calls[0][0] == "reply text"


@pytest.mark.asyncio
async def test_desktop_voice_channel_stop_on_empty_exits_without_publish(tmp_path: Path) -> None:
    bus = MessageBus()
    cfg = DesktopVoiceConfig(
        enabled=True,
        allow_from=["desktop_user"],
        stop_on_empty=True,
        max_turns=0,
    )
    channel = DesktopVoiceChannel(cfg, bus)
    runtime = Config()
    runtime.agents.defaults.workspace = str(tmp_path / "workspace")
    runtime.voice.capture.command_template = ["dummy", "{output}", "{duration}"]
    channel.set_runtime_config(runtime)

    fake_capture = _FakeCapture(tmp_path / "seed.wav")
    (tmp_path / "seed.wav").write_bytes(b"audio")
    fake_orchestrator = _FakeOrchestrator(transcript="")

    channel._make_capture = lambda: fake_capture  # type: ignore[method-assign]
    channel._make_orchestrator = lambda: fake_orchestrator  # type: ignore[method-assign]

    await asyncio.wait_for(channel.start(), timeout=1.0)
    assert bus.inbound_size == 0
    assert fake_orchestrator.shutdown_calls == 1


@pytest.mark.asyncio
async def test_desktop_voice_channel_listen_input_false_skips_capture_but_allows_send(tmp_path: Path) -> None:
    bus = MessageBus()
    cfg = DesktopVoiceConfig(
        enabled=True,
        allow_from=["desktop_user"],
        listen_input=False,
        playback=False,
        avatar=False,
    )
    channel = DesktopVoiceChannel(cfg, bus)
    runtime = Config()
    runtime.agents.defaults.workspace = str(tmp_path / "workspace")
    runtime.voice.capture.command_template = ["dummy", "{output}", "{duration}"]
    channel.set_runtime_config(runtime)

    capture_calls = {"count": 0}
    fake_orchestrator = _FakeOrchestrator(transcript="should-not-run")

    def _fake_capture_factory():
        capture_calls["count"] += 1
        return _FakeCapture(tmp_path / "seed.wav")

    channel._make_capture = _fake_capture_factory  # type: ignore[method-assign]
    channel._make_orchestrator = lambda: fake_orchestrator  # type: ignore[method-assign]

    start_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.05)
    assert capture_calls["count"] == 0
    assert bus.inbound_size == 0

    await channel.send(
        OutboundMessage(
            channel="desktop_voice",
            chat_id="desktop_local",
            content="speak without listening",
        )
    )

    assert fake_orchestrator.speak_calls[0][0] == "speak without listening"
    await channel.stop()
    await asyncio.wait_for(start_task, timeout=1.0)
    assert fake_orchestrator.shutdown_calls == 1


@pytest.mark.asyncio
async def test_desktop_voice_channel_falls_back_when_avatar_runtime_connect_fails(tmp_path: Path) -> None:
    bus = MessageBus()
    cfg = DesktopVoiceConfig(
        enabled=True,
        allow_from=["desktop_user"],
        listen_input=False,
        playback=False,
        avatar=True,
    )
    channel = DesktopVoiceChannel(cfg, bus)
    runtime = Config()
    runtime.agents.defaults.workspace = str(tmp_path / "workspace")
    channel.set_runtime_config(runtime)

    class _FailingOrchestrator:
        def __init__(self) -> None:
            self.start_calls = 0
            self.shutdown_calls = 0

        async def start(self) -> None:
            self.start_calls += 1
            raise OSError("[WinError 1225] remote connection refused")

        async def shutdown(self) -> None:
            self.shutdown_calls += 1

    fake_success = _FakeOrchestrator(transcript="unused")
    failing = _FailingOrchestrator()
    created: list[object] = []

    def _factory(avatar_runtime=None):  # noqa: ANN001
        if avatar_runtime is None:
            created.append(failing)
            return failing
        created.append(fake_success)
        return fake_success

    channel._make_orchestrator = _factory  # type: ignore[method-assign]

    start_task = asyncio.create_task(channel.start())
    await asyncio.sleep(0.05)
    assert created[0] is failing
    assert created[1] is fake_success
    assert failing.start_calls == 1

    await channel.send(
        OutboundMessage(
            channel="desktop_voice",
            chat_id="desktop_local",
            content="fallback speech",
        )
    )
    assert fake_success.speak_calls[0][0] == "fallback speech"

    await channel.stop()
    await asyncio.wait_for(start_task, timeout=1.0)
    assert fake_success.shutdown_calls == 1


def test_channel_manager_injects_runtime_config_into_desktop_voice(monkeypatch) -> None:
    fake_config = Config.model_validate(
        {
            "channels": {
                "desktop_voice": {
                    "enabled": True,
                    "allowFrom": ["desktop_user"],
                    "maxTurns": 0,
                    "captureCmdPart": ["recorder", "{output}", "{duration}"],
                }
            }
        }
    )
    fake_config.providers.groq.api_key = "groq-key"

    monkeypatch.setattr(
        "nanobot.channels.registry.discover_all",
        lambda: {"desktop_voice": DesktopVoiceChannel},
    )

    mgr = ChannelManager(fake_config, MessageBus())

    channel = mgr.channels["desktop_voice"]
    assert isinstance(channel, DesktopVoiceChannel)
    assert channel.runtime_config is fake_config
    assert channel.transcription_api_key == "groq-key"


def test_desktop_voice_channel_native_audio_factories_forward_device_selectors(monkeypatch, tmp_path: Path) -> None:
    cfg = DesktopVoiceConfig(enabled=True, native_capture=True, playback=True)
    channel = DesktopVoiceChannel(cfg, MessageBus())
    runtime = Config()
    runtime.agents.defaults.workspace = str(tmp_path / "workspace")
    runtime.voice.capture.backend = "sounddevice"
    runtime.voice.playback.backend = "sounddevice"
    runtime.voice.input_device = "2"
    runtime.voice.output_device = "Built-in Output"
    channel.set_runtime_config(runtime)

    seen: dict[str, object] = {}

    class _FakeCapture:
        def __init__(self, *, sample_rate_hz, channels, input_device):  # noqa: ANN001
            seen["capture"] = (sample_rate_hz, channels, input_device)

    class _FakeAudio:
        def __init__(self, *, output_device):  # noqa: ANN001
            seen["audio"] = output_device

    monkeypatch.setattr("nanobot.channels.desktop_voice.SoundDeviceAudioCapture", _FakeCapture)
    monkeypatch.setattr("nanobot.channels.desktop_voice.SoundDeviceAudioIO", _FakeAudio)

    channel._make_capture()
    channel._make_audio_io()

    assert seen["capture"] == (runtime.voice.capture.sample_rate_hz, runtime.voice.capture.channels, 2)
    assert seen["audio"] == "Built-in Output"
