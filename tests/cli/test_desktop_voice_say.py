from pathlib import Path
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.config.schema import Config

runner = CliRunner()


def test_desktop_voice_say_runs_output_chain(monkeypatch, tmp_path: Path) -> None:
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    seen: dict[str, object] = {}

    class _FakeTTS:
        pass

    class _FakeOrchestrator:
        def __init__(self, **kwargs) -> None:
            seen["kwargs"] = kwargs
            self.start = AsyncMock(return_value=None)
            self.shutdown = AsyncMock(return_value=None)

        async def speak_text(self, text, output_path, *, voice=None, expression=None, motion=None):  # noqa: ANN001
            _ = voice, expression, motion
            path = Path(output_path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"tts")
            return type("TTS", (), {"path": path})()

    monkeypatch.setattr("nanobot.cli.commands._load_runtime_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr("nanobot.cli.commands.sync_workspace_templates", lambda _path: None)
    monkeypatch.setattr("nanobot.cli.commands._make_tts_provider", lambda _config: _FakeTTS())
    monkeypatch.setattr("nanobot.cli.commands._make_avatar_runtime", lambda _config, enabled: ("avatar", enabled))
    monkeypatch.setattr("nanobot.cli.commands._make_audio_io_from_config", lambda _config, enabled: ("audio", enabled))
    monkeypatch.setattr("nanobot.avatar.DesktopVoiceAvatarOrchestrator", _FakeOrchestrator)

    result = runner.invoke(
        app,
        [
            "desktop-voice-say",
            "--text",
            "你好，这是桌宠输出测试",
            "--output-dir",
            str(tmp_path / "out"),
            "--playback",
            "--avatar",
        ],
    )

    assert result.exit_code == 0
    assert "Spoken text:" in result.stdout
    assert "你好，这是桌宠输出测试" in result.stdout
    assert "Synthesized audio:" in result.stdout
    assert seen["kwargs"]["tts"].__class__ is _FakeTTS
    assert seen["kwargs"]["avatar"] == ("avatar", True)
    assert seen["kwargs"]["audio_io"] == ("audio", True)
