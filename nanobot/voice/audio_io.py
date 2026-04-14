"""Platform-neutral audio I/O abstractions for desktop voice flow."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Awaitable, Callable

from nanobot.voice.devices import resolve_audio_device


class BaseAudioIO(ABC):
    """Abstract audio adapter (playback/capture control)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter identifier."""

    @abstractmethod
    async def play_file(self, file_path: str | Path) -> None:
        """Play one synthesized audio file."""

    @abstractmethod
    async def stop_playback(self) -> None:
        """Stop current playback if active."""


class NullAudioIO(BaseAudioIO):
    """No-op audio adapter used for tests and headless mode."""

    def __init__(self) -> None:
        self.played_files: list[Path] = []
        self.stop_count = 0

    @property
    def name(self) -> str:
        return "none"

    async def play_file(self, file_path: str | Path) -> None:
        self.played_files.append(Path(file_path).expanduser().resolve())

    async def stop_playback(self) -> None:
        self.stop_count += 1


class CommandAudioIO(BaseAudioIO):
    """Cross-platform audio playback via external command."""

    def __init__(
        self,
        *,
        play_command: list[str] | None = None,
        process_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    ) -> None:
        self.play_command = play_command or [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "quiet",
            "{file}",
        ]
        self._process_factory = process_factory
        self._process: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "command"

    async def play_file(self, file_path: str | Path) -> None:
        path = Path(file_path).expanduser().resolve()
        cmd = [part.replace("{file}", str(path)) for part in self.play_command]

        async with self._lock:
            await self._stop_locked()
            process = await self._process_factory(*cmd)
            self._process = process

        await process.wait()

        async with self._lock:
            if self._process is process:
                self._process = None

    async def stop_playback(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        if self._process is None:
            return
        return_code = getattr(self._process, "returncode", None)
        if return_code is None:
            terminate = getattr(self._process, "terminate", None)
            if callable(terminate):
                terminate()
            wait = getattr(self._process, "wait", None)
            if callable(wait):
                try:
                    await asyncio.wait_for(wait(), timeout=1.0)
                except TimeoutError:
                    kill = getattr(self._process, "kill", None)
                    if callable(kill):
                        kill()
                        await wait()
        self._process = None


class SoundDeviceAudioIO(BaseAudioIO):
    """Native cross-platform playback via sounddevice + soundfile."""

    def __init__(
        self,
        *,
        output_device: int | str | None = None,
        module_loader: Callable[[str], ModuleType] = import_module,
    ) -> None:
        self.output_device = output_device
        try:
            self._sounddevice = module_loader("sounddevice")
            self._soundfile = module_loader("soundfile")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "sounddevice and soundfile are required for SoundDeviceAudioIO. "
                "Install with: pip install sounddevice soundfile"
            ) from exc

        self._lock = asyncio.Lock()
        self._playing = False

    @property
    def name(self) -> str:
        return "sounddevice"

    async def play_file(self, file_path: str | Path) -> None:
        path = Path(file_path).expanduser().resolve()
        async with self._lock:
            self._playing = True
        try:
            await asyncio.to_thread(self._play_sync, path)
        finally:
            async with self._lock:
                self._playing = False

    async def stop_playback(self) -> None:
        self._sounddevice.stop()
        async with self._lock:
            self._playing = False

    def _play_sync(self, path: Path) -> None:
        device = resolve_audio_device(
            self.output_device,
            kind="output",
            module_loader=lambda _name: self._sounddevice,
        )
        data, sample_rate = self._soundfile.read(str(path), dtype="float32")
        self._sounddevice.play(data, sample_rate, device=device)
        self._sounddevice.wait()
