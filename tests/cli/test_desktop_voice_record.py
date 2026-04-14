from pathlib import Path
from unittest.mock import AsyncMock

import click
import pytest
from typer.testing import CliRunner

from nanobot.cli.commands import _make_audio_capture_from_config, app
from nanobot.config.schema import Config

runner = CliRunner()


def test_make_audio_capture_from_config_requires_template() -> None:
    config = Config()

    with pytest.raises(click.exceptions.Exit):
        _make_audio_capture_from_config(config)


def test_desktop_voice_record_runs_capture_based_flow(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.voice.capture.command_template = ["recorder", "--out", "{output}", "--duration", "{duration}"]
    synthesized = tmp_path / "out" / "reply.mp3"

    fake_orchestrator_instances = []
    seen: dict[str, object] = {}

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            self.store_path = store_path

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs
            self.close_mcp = AsyncMock(return_value=None)

        async def process_direct(self, content: str, **kwargs) -> str:
            _ = kwargs
            return f"reply:{content}"

    class _FakeCapture:
        pass

    class _FakeOrchestrator:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.start = AsyncMock(return_value=None)
            self.shutdown = AsyncMock(return_value=None)
            fake_orchestrator_instances.append(self)

    class _FakeRunner:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def run_turn_from_capture(self, capture, *, duration_s=None, language=None, voice=None):  # noqa: ANN001
            _ = duration_s, language, voice
            assert isinstance(capture, _FakeCapture)
            synthesized.parent.mkdir(parents=True, exist_ok=True)
            synthesized.write_bytes(b"tts")
            input_audio = tmp_path / "out" / "capture.wav"
            input_audio.write_bytes(b"capture")
            return type(
                "VoiceTurnResult",
                (),
                {
                    "input_audio": input_audio.resolve(),
                    "transcript": "captured transcript",
                    "response": "captured response",
                    "synthesized_audio": synthesized.resolve(),
                },
            )()

    monkeypatch.setattr("nanobot.cli.commands._load_runtime_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr("nanobot.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_stt_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_tts_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_avatar_runtime", lambda _config, enabled: ("avatar", enabled))
    monkeypatch.setattr("nanobot.cli.commands._make_audio_io_from_config", lambda _config, enabled: ("audio", enabled))
    def _make_capture(_config, backend=None, command_template=None):  # noqa: ANN001
        seen["backend"] = backend
        seen["command_template"] = command_template
        return _FakeCapture()

    monkeypatch.setattr("nanobot.cli.commands._make_audio_capture_from_config", _make_capture)
    monkeypatch.setattr("nanobot.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("nanobot.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("nanobot.avatar.DesktopVoiceAvatarOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr("nanobot.voice.runtime.DesktopVoiceTurnRunner", _FakeRunner)

    result = runner.invoke(
        app,
        [
            "desktop-voice-record",
            "--output-dir",
            str(tmp_path / "out"),
            "--duration",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert "captured transcript" in result.stdout
    assert "captured response" in result.stdout
    compact = result.stdout.replace("\r", "").replace("\n", "")
    assert str(synthesized.resolve()) in compact
    assert seen["backend"] is None
    assert len(fake_orchestrator_instances) == 1
    fake_orchestrator_instances[0].start.assert_awaited_once()
    fake_orchestrator_instances[0].shutdown.assert_awaited_once()


def test_desktop_voice_record_native_capture_selects_sounddevice_backend(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    seen: dict[str, object] = {}

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            self.store_path = store_path

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs
            self.close_mcp = AsyncMock(return_value=None)

        async def process_direct(self, content: str, **kwargs) -> str:
            _ = content, kwargs
            return "ok"

    class _FakeOrchestrator:
        def __init__(self, **kwargs) -> None:
            _ = kwargs
            self.start = AsyncMock(return_value=None)
            self.shutdown = AsyncMock(return_value=None)

    class _FakeRunner:
        def __init__(self, **kwargs) -> None:
            _ = kwargs

        async def run_turn_from_capture(self, capture, *, duration_s=None, language=None, voice=None):  # noqa: ANN001
            _ = capture, duration_s, language, voice
            return type(
                "VoiceTurnResult",
                (),
                {
                    "input_audio": (tmp_path / "capture.wav").resolve(),
                    "transcript": "native transcript",
                    "response": "native response",
                    "synthesized_audio": (tmp_path / "reply.mp3").resolve(),
                },
            )()

    def _make_capture(_config, backend=None, command_template=None):  # noqa: ANN001
        seen["backend"] = backend
        seen["command_template"] = command_template
        return object()

    monkeypatch.setattr("nanobot.cli.commands._load_runtime_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr("nanobot.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_stt_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_tts_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_avatar_runtime", lambda _config, enabled: ("avatar", enabled))
    monkeypatch.setattr("nanobot.cli.commands._make_audio_io_from_config", lambda _config, enabled: ("audio", enabled))
    monkeypatch.setattr("nanobot.cli.commands._make_audio_capture_from_config", _make_capture)
    monkeypatch.setattr("nanobot.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("nanobot.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("nanobot.avatar.DesktopVoiceAvatarOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr("nanobot.voice.runtime.DesktopVoiceTurnRunner", _FakeRunner)

    result = runner.invoke(app, ["desktop-voice-record", "--native-capture"])

    assert result.exit_code == 0
    assert seen["backend"] == "sounddevice"


def test_make_audio_io_from_config_selects_sounddevice_backend(monkeypatch) -> None:
    from nanobot.cli.commands import _make_audio_io_from_config

    config = Config()
    config.voice.playback.backend = "sounddevice"
    config.voice.output_device = "Built-in Output"

    class _FakeSoundDeviceAudioIO:
        def __init__(self, *, output_device):  # noqa: ANN001
            self.output_device = output_device

    monkeypatch.setattr("nanobot.voice.SoundDeviceAudioIO", _FakeSoundDeviceAudioIO)

    audio = _make_audio_io_from_config(config, enabled=True)

    assert isinstance(audio, _FakeSoundDeviceAudioIO)
    assert audio.output_device == "Built-in Output"


def test_apply_voice_device_overrides_updates_runtime_config() -> None:
    from nanobot.cli.commands import _apply_voice_device_overrides

    config = Config()
    updated = _apply_voice_device_overrides(
        config,
        input_device="2",
        output_device="Built-in Output",
    )

    assert updated.voice.input_device == "2"
    assert updated.voice.output_device == "Built-in Output"
