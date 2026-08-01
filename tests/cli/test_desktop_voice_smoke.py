from pathlib import Path
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.config.schema import Config

runner = CliRunner()


def test_desktop_voice_smoke_runs_file_based_flow(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    input_audio = tmp_path / "input.wav"
    input_audio.write_bytes(b"audio")
    synthesized = tmp_path / "out" / "reply.mp3"

    fake_orchestrator_instances = []

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

    class _FakeOrchestrator:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.start = AsyncMock(return_value=None)
            self.shutdown = AsyncMock(return_value=None)
            fake_orchestrator_instances.append(self)

    class _FakeRunner:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def run_turn_from_audio_file(self, input_audio, *, language=None, voice=None):  # noqa: ANN001
            _ = language, voice
            synthesized.parent.mkdir(parents=True, exist_ok=True)
            synthesized.write_bytes(b"tts")
            return type(
                "VoiceTurnResult",
                (),
                {
                    "input_audio": Path(input_audio).resolve(),
                    "transcript": "test transcript",
                    "response": "test response",
                    "synthesized_audio": synthesized.resolve(),
                },
            )()

    monkeypatch.setattr("nanobot.cli.commands._load_runtime_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr("nanobot.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_stt_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_tts_provider", lambda _config: object())
    monkeypatch.setattr("nanobot.cli.commands._make_avatar_runtime", lambda _config, enabled: ("avatar", enabled))
    monkeypatch.setattr("nanobot.cli.commands._make_audio_io", lambda enabled: ("audio", enabled))
    monkeypatch.setattr("nanobot.bus.queue.MessageBus", lambda: object())
    monkeypatch.setattr("nanobot.cron.service.CronService", _FakeCron)
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", _FakeAgentLoop)
    monkeypatch.setattr("nanobot.avatar.DesktopVoiceAvatarOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr("nanobot.voice.runtime.DesktopVoiceTurnRunner", _FakeRunner)

    result = runner.invoke(
        app,
        [
            "desktop-voice-smoke",
            "--input-audio",
            str(input_audio),
            "--output-dir",
            str(tmp_path / "out"),
            "--playback",
            "--avatar",
        ],
    )

    assert result.exit_code == 0
    assert "Input audio:" in result.stdout
    assert "Transcript:" in result.stdout
    assert "test transcript" in result.stdout
    assert "Synthesized audio:" in result.stdout
    compact = result.stdout.replace("\r", "").replace("\n", "")
    assert str(synthesized.resolve()) in compact
    assert len(fake_orchestrator_instances) == 1
    fake_orchestrator_instances[0].start.assert_awaited_once()
    fake_orchestrator_instances[0].shutdown.assert_awaited_once()
