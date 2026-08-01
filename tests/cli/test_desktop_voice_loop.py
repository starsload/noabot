from pathlib import Path
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.config.schema import Config

runner = CliRunner()


def test_desktop_voice_devices_lists_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "nanobot.voice.list_audio_devices",
        lambda **kwargs: [
            type("Dev", (), {"index": 0, "name": "Mic 1", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 16000.0, "is_default_input": True, "is_default_output": False})(),
            type("Dev", (), {"index": 1, "name": "Speaker 1", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0, "is_default_input": False, "is_default_output": True})(),
        ],
    )

    result = runner.invoke(app, ["desktop-voice-devices"])

    assert result.exit_code == 0
    assert "Audio Devices" in result.stdout
    assert "Mic 1" in result.stdout
    assert "Speaker 1" in result.stdout


def test_desktop_voice_loop_runs_multiple_turns(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    seen: dict[str, int] = {"turns": 0}

    class _FakeCron:
        def __init__(self, store_path: Path) -> None:
            self.store_path = store_path

    class _FakeAgentLoop:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs
            self.close_mcp = AsyncMock(return_value=None)

        async def process_direct(self, content: str, **kwargs) -> str:
            _ = content, kwargs
            return "loop-response"

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
            seen["turns"] += 1
            idx = seen["turns"]
            return type(
                "VoiceTurnResult",
                (),
                {
                    "input_audio": (tmp_path / f"capture-{idx}.wav").resolve(),
                    "transcript": f"transcript-{idx}",
                    "response": f"response-{idx}",
                    "synthesized_audio": (tmp_path / f"reply-{idx}.mp3").resolve(),
                },
            )()

    monkeypatch.setattr("nanobot.cli.commands._load_runtime_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr("nanobot.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_stt_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_tts_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_avatar_runtime", lambda _config, enabled: ("avatar", enabled))
    monkeypatch.setattr("nanobot.cli.commands._make_audio_io_from_config", lambda _config, enabled: ("audio", enabled))
    monkeypatch.setattr("nanobot.cli.commands._make_audio_capture_from_config", lambda _config, backend=None, command_template=None: object())
    monkeypatch.setattr("nanobot.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("nanobot.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("nanobot.avatar.DesktopVoiceAvatarOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr("nanobot.voice.runtime.DesktopVoiceTurnRunner", _FakeRunner)

    result = runner.invoke(app, ["desktop-voice-loop", "--turns", "2"])

    assert result.exit_code == 0
    assert "Turn 1/2" in result.stdout
    assert "Turn 2/2" in result.stdout
    assert "transcript-1" in result.stdout
    assert "transcript-2" in result.stdout
    assert seen["turns"] == 2
