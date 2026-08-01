"""Platform-neutral audio capture abstractions."""

from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable

from nanobot.voice.devices import resolve_audio_device


class BaseAudioCapture(ABC):
    """Abstract audio capture adapter (e.g., microphone)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter identifier."""

    @abstractmethod
    async def capture_to_file(
        self,
        output_path: str | Path,
        *,
        duration_s: float = 5.0,
    ) -> Path:
        """Capture one audio segment into *output_path*."""


class FileAudioCapture(BaseAudioCapture):
    """Capture adapter that reuses an existing audio file (for tests/smoke runs)."""

    def __init__(self, source_file: str | Path):
        self.source_file = Path(source_file).expanduser().resolve()

    @property
    def name(self) -> str:
        return "file"

    async def capture_to_file(
        self,
        output_path: str | Path,
        *,
        duration_s: float = 5.0,
    ) -> Path:
        _ = duration_s
        if not self.source_file.exists():
            raise FileNotFoundError(f"Source audio file not found: {self.source_file}")
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.source_file, output)
        return output


class CommandAudioCapture(BaseAudioCapture):
    """Capture audio using an external command template.

    The command supports placeholders:
    - ``{output}``: output audio file path
    - ``{duration}``: duration in seconds
    """

    def __init__(
        self,
        command_template: list[str],
        *,
        process_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    ) -> None:
        if not command_template:
            raise ValueError("command_template cannot be empty")
        self.command_template = command_template
        self._process_factory = process_factory

    @property
    def name(self) -> str:
        return "command_capture"

    async def capture_to_file(
        self,
        output_path: str | Path,
        *,
        duration_s: float = 5.0,
    ) -> Path:
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            part.replace("{output}", str(output)).replace("{duration}", str(duration_s))
            for part in self.command_template
        ]
        process = await self._process_factory(*cmd)
        return_code = await process.wait()
        if return_code != 0:
            raise RuntimeError(f"Capture command failed with exit code {return_code}: {' '.join(cmd)}")
        if not output.exists():
            raise RuntimeError(f"Capture command finished but output file missing: {output}")
        return output


class SoundDeviceAudioCapture(BaseAudioCapture):
    """Native cross-platform microphone capture via sounddevice + soundfile."""

    def __init__(
        self,
        *,
        sample_rate_hz: int = 16_000,
        channels: int = 1,
        input_device: int | str | None = None,
        module_loader: Callable[[str], ModuleType] = import_module,
    ) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.channels = channels
        self.input_device = input_device
        try:
            self._sounddevice = module_loader("sounddevice")
            self._soundfile = module_loader("soundfile")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "sounddevice and soundfile are required for SoundDeviceAudioCapture. "
                "Install with: pip install sounddevice soundfile"
            ) from exc

    @property
    def name(self) -> str:
        return "sounddevice"

    async def capture_to_file(
        self,
        output_path: str | Path,
        *,
        duration_s: float = 5.0,
    ) -> Path:
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        frames = int(duration_s * self.sample_rate_hz)
        await asyncio.to_thread(self._capture_sync, output, frames)
        return output

    def _capture_sync(self, output: Path, frames: int) -> None:
        device = resolve_audio_device(
            self.input_device,
            kind="input",
            module_loader=lambda _name: self._sounddevice,
        )
        data = self._sounddevice.rec(
            frames,
            samplerate=self.sample_rate_hz,
            channels=self.channels,
            dtype="float32",
            device=device,
        )
        self._sounddevice.wait()
        self._soundfile.write(str(output), data, self.sample_rate_hz)
