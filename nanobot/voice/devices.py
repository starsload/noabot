"""Audio device discovery utilities."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import Callable


@dataclass(slots=True)
class AudioDeviceInfo:
    """Normalized audio device metadata."""

    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float | None = None
    is_default_input: bool = False
    is_default_output: bool = False


def parse_device_selector(value: str | None) -> int | str | None:
    """Parse a user-supplied device selector.

    - empty/None -> None
    - numeric string -> int index
    - other string -> device name substring / exact name (left to backend)
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"default", "auto"}:
        return None
    if text.isdigit():
        return int(text)
    return text


def resolve_audio_device(
    selector: int | str | None,
    *,
    kind: str,
    module_loader: Callable[[str], ModuleType] = import_module,
) -> int | None:
    """Resolve a device selector to a concrete sounddevice index.

    ``kind`` must be ``"input"`` or ``"output"``.
    """
    if kind not in {"input", "output"}:
        raise ValueError("kind must be 'input' or 'output'")

    try:
        sounddevice = module_loader("sounddevice")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "sounddevice is required for audio device resolution. Install with: pip install sounddevice"
        ) from exc

    default_pair = getattr(getattr(sounddevice, "default", None), "device", None)
    default_index = None
    if isinstance(default_pair, (list, tuple)) and len(default_pair) >= 2:
        default_index = int(default_pair[0] if kind == "input" else default_pair[1])

    if selector is None:
        return default_index

    if isinstance(selector, int):
        return selector

    devices = sounddevice.query_devices()
    selector_text = str(selector).strip().lower()

    # Exact case-insensitive match first.
    for idx, raw in enumerate(devices):
        name = str(raw.get("name", "")).strip()
        if name.lower() == selector_text:
            return idx

    # Then substring match.
    for idx, raw in enumerate(devices):
        name = str(raw.get("name", "")).strip()
        if selector_text in name.lower():
            return idx

    raise RuntimeError(f"No {kind} audio device matched selector: {selector}")


def list_audio_devices(
    *,
    input_only: bool = False,
    output_only: bool = False,
    module_loader: Callable[[str], ModuleType] = import_module,
) -> list[AudioDeviceInfo]:
    """List audio devices via sounddevice if available."""
    if input_only and output_only:
        raise ValueError("input_only and output_only cannot both be True")

    try:
        sounddevice = module_loader("sounddevice")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "sounddevice is required for audio device listing. Install with: pip install sounddevice"
        ) from exc

    devices = sounddevice.query_devices()
    default_pair = getattr(getattr(sounddevice, "default", None), "device", None)
    default_input = None
    default_output = None
    if isinstance(default_pair, (list, tuple)) and len(default_pair) >= 2:
        default_input = int(default_pair[0])
        default_output = int(default_pair[1])
    out: list[AudioDeviceInfo] = []
    for idx, raw in enumerate(devices):
        max_in = int(raw.get("max_input_channels", 0) or 0)
        max_out = int(raw.get("max_output_channels", 0) or 0)
        if input_only and max_in <= 0:
            continue
        if output_only and max_out <= 0:
            continue
        out.append(
            AudioDeviceInfo(
                index=idx,
                name=str(raw.get("name", f"device-{idx}")),
                max_input_channels=max_in,
                max_output_channels=max_out,
                default_samplerate=raw.get("default_samplerate"),
                is_default_input=(default_input == idx),
                is_default_output=(default_output == idx),
            )
        )
    return out
