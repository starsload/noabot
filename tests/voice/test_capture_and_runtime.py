from pathlib import Path

import pytest

from nanobot.voice.capture import CommandAudioCapture, FileAudioCapture, SoundDeviceAudioCapture
from nanobot.voice.runtime import DesktopVoiceTurnRunner, infer_expression_from_text


class _FakeProcess:
    def __init__(self, output: Path | None = None) -> None:
        self.output = output

    async def wait(self) -> int:
        if self.output is not None:
            self.output.write_bytes(b"captured")
        return 0


class _FakeAgent:
    def __init__(self, response: str = "你好，很高兴见到你") -> None:
        self.response = response
        self.calls: list[tuple[str, str, str, str]] = []

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        **kwargs,
    ) -> str:
        _ = kwargs
        self.calls.append((content, session_key, channel, chat_id))
        return self.response


class _FakeOrchestrator:
    def __init__(self, transcript: str = "今天天气不错") -> None:
        self.transcript = transcript
        self.transcribe_calls: list[Path] = []
        self.speak_calls: list[tuple[str, Path, str | None, str | None]] = []

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
async def test_file_audio_capture_copies_source(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    output = tmp_path / "copied.wav"

    capture = FileAudioCapture(source)
    result = await capture.capture_to_file(output)

    assert result == output.resolve()
    assert output.read_bytes() == b"audio"


@pytest.mark.asyncio
async def test_command_audio_capture_populates_placeholders(tmp_path: Path) -> None:
    output = tmp_path / "captured.wav"
    captured_args: list[tuple[str, ...]] = []

    async def _factory(*args):  # noqa: ANN002
        captured_args.append(tuple(args))
        return _FakeProcess(output=output)

    capture = CommandAudioCapture(
        ["recorder", "--out", "{output}", "--duration", "{duration}"],
        process_factory=_factory,
    )
    result = await capture.capture_to_file(output, duration_s=3.5)

    assert captured_args == [("recorder", "--out", str(output.resolve()), "--duration", "3.5")]
    assert result == output.resolve()
    assert output.exists()


@pytest.mark.asyncio
async def test_sounddevice_audio_capture_uses_injected_modules(tmp_path: Path) -> None:
    output = tmp_path / "native.wav"

    class _FakeSoundDevice:
        def __init__(self) -> None:
            self.calls: list[tuple[int, int, int, str, object]] = []

        def rec(self, frames: int, *, samplerate: int, channels: int, dtype: str, device=None):  # noqa: ANN001
            self.calls.append((frames, samplerate, channels, dtype, device))
            return b"fake-frames"

        def wait(self) -> None:
            return None

    class _FakeSoundFile:
        def __init__(self) -> None:
            self.writes: list[tuple[str, bytes, int]] = []

        def write(self, path: str, data, sample_rate: int) -> None:  # noqa: ANN001
            self.writes.append((path, data, sample_rate))
            Path(path).write_bytes(b"captured")

    sd = _FakeSoundDevice()
    sf = _FakeSoundFile()

    def _loader(name: str):
        if name == "sounddevice":
            return sd
        if name == "soundfile":
            return sf
        raise ModuleNotFoundError(name)

    capture = SoundDeviceAudioCapture(
        sample_rate_hz=16000,
        channels=1,
        input_device=2,
        module_loader=_loader,
    )
    result = await capture.capture_to_file(output, duration_s=2.0)

    assert result == output.resolve()
    assert sd.calls == [(32000, 16000, 1, "float32", 2)]
    assert sf.writes == [(str(output.resolve()), b"fake-frames", 16000)]
    assert output.exists()


@pytest.mark.asyncio
async def test_voice_turn_runner_runs_full_turn_from_audio_file(tmp_path: Path) -> None:
    orchestrator = _FakeOrchestrator()
    agent = _FakeAgent()
    input_audio = tmp_path / "input.wav"
    input_audio.write_bytes(b"input")

    runner = DesktopVoiceTurnRunner(
        orchestrator=orchestrator,
        agent=agent,
        output_dir=tmp_path / "out",
        session_key="desktop:local",
        channel="cli",
        chat_id="desktop-local",
    )

    result = await runner.run_turn_from_audio_file(input_audio, language="zh", voice="voice-a")

    assert result.transcript == "今天天气不错"
    assert result.response == "你好，很高兴见到你"
    assert result.input_audio == input_audio.resolve()
    assert result.synthesized_audio.exists()
    assert agent.calls == [("今天天气不错", "desktop:local", "cli", "desktop-local")]
    assert orchestrator.speak_calls[0][2] == "voice-a"
    assert orchestrator.speak_calls[0][3] == "smile"


def test_infer_expression_from_text_maps_expected_cases() -> None:
    assert infer_expression_from_text("谢谢你，太好了") == "smile"
    assert infer_expression_from_text("抱歉我刚刚没听清") == "sad"
    assert infer_expression_from_text("这是一个普通陈述句") is None
