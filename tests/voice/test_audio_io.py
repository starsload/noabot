import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.voice.audio_io import CommandAudioIO, NullAudioIO, SoundDeviceAudioIO


@pytest.mark.asyncio
async def test_null_audio_io_records_playback_and_stop(tmp_path: Path) -> None:
    audio = NullAudioIO()
    clip = tmp_path / "sample.mp3"
    clip.write_bytes(b"audio")

    await audio.play_file(clip)
    await audio.stop_playback()

    assert audio.played_files == [clip.resolve()]
    assert audio.stop_count == 1


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self._event = asyncio.Event()

    async def wait(self) -> int:
        await self._event.wait()
        return self.returncode or 0

    def finish(self, code: int = 0) -> None:
        self.returncode = code
        self._event.set()

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.finish(0)

    def kill(self) -> None:
        self.kill_calls += 1
        self.finish(-9)


@pytest.mark.asyncio
async def test_command_audio_io_replaces_file_placeholder_and_waits(tmp_path: Path) -> None:
    clip = tmp_path / "sample.mp3"
    clip.write_bytes(b"audio")
    captured_args: list[tuple[str, ...]] = []
    proc = _FakeProcess()

    async def _factory(*args):  # noqa: ANN002
        captured_args.append(tuple(args))
        proc.finish(0)
        return proc

    audio = CommandAudioIO(play_command=["player", "--input", "{file}"], process_factory=_factory)
    await audio.play_file(clip)

    assert captured_args == [("player", "--input", str(clip.resolve()))]


@pytest.mark.asyncio
async def test_command_audio_io_stop_playback_terminates_running_process(tmp_path: Path) -> None:
    clip = tmp_path / "sample.mp3"
    clip.write_bytes(b"audio")
    proc = _FakeProcess()

    async def _factory(*args):  # noqa: ANN002
        _ = args
        return proc

    audio = CommandAudioIO(process_factory=_factory)
    task = asyncio.create_task(audio.play_file(clip))
    await asyncio.sleep(0)  # allow play_file to start and create process
    await audio.stop_playback()
    await task

    assert proc.terminate_calls == 1


@pytest.mark.asyncio
async def test_sounddevice_audio_io_uses_injected_modules(tmp_path: Path) -> None:
    clip = tmp_path / "sample.wav"
    clip.write_bytes(b"audio")

    class _FakeSoundDevice:
        default = SimpleNamespace(device=(0, 1))

        def __init__(self) -> None:
            self.play_calls: list[tuple[object, int, object]] = []
            self.wait_calls = 0
            self.stop_calls = 0

        @staticmethod
        def query_devices():
            return [
                {"name": "Mic 1"},
                {"name": "Built-in Output"},
            ]

        def play(self, data, sample_rate: int, device=None) -> None:  # noqa: ANN001
            self.play_calls.append((data, sample_rate, device))

        def wait(self) -> None:
            self.wait_calls += 1

        def stop(self) -> None:
            self.stop_calls += 1

    class _FakeSoundFile:
        def __init__(self) -> None:
            self.read_calls: list[tuple[str, str]] = []

        def read(self, path: str, dtype: str = "float32"):  # noqa: ANN001
            self.read_calls.append((path, dtype))
            return b"frames", 16000

    sd = _FakeSoundDevice()
    sf = _FakeSoundFile()

    def _loader(name: str):
        if name == "sounddevice":
            return sd
        if name == "soundfile":
            return sf
        raise ModuleNotFoundError(name)

    audio = SoundDeviceAudioIO(output_device="Built-in Output", module_loader=_loader)
    await audio.play_file(clip)
    await audio.stop_playback()

    assert sf.read_calls == [(str(clip.resolve()), "float32")]
    assert sd.play_calls == [(b"frames", 16000, 1)]
    assert sd.wait_calls == 1
    assert sd.stop_calls == 1


def test_sounddevice_audio_io_dependency_error_message() -> None:
    with pytest.raises(RuntimeError) as exc:
        SoundDeviceAudioIO(module_loader=lambda _: (_ for _ in ()).throw(ModuleNotFoundError("sounddevice")))

    assert "pip install sounddevice soundfile" in str(exc.value)
