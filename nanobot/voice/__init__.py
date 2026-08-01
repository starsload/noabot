"""Voice runtime abstractions with lazy exports to avoid import cycles."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "BaseAudioCapture",
    "CommandAudioCapture",
    "FileAudioCapture",
    "SoundDeviceAudioCapture",
    "AudioDeviceInfo",
    "list_audio_devices",
    "parse_device_selector",
    "resolve_audio_device",
    "BaseAudioIO",
    "CommandAudioIO",
    "NullAudioIO",
    "SoundDeviceAudioIO",
    "DesktopVoiceTurnRunner",
    "VoiceTurnResult",
    "infer_expression_from_text",
]

_LAZY_IMPORTS = {
    "BaseAudioCapture": ".capture",
    "CommandAudioCapture": ".capture",
    "FileAudioCapture": ".capture",
    "SoundDeviceAudioCapture": ".capture",
    "AudioDeviceInfo": ".devices",
    "list_audio_devices": ".devices",
    "parse_device_selector": ".devices",
    "resolve_audio_device": ".devices",
    "BaseAudioIO": ".audio_io",
    "CommandAudioIO": ".audio_io",
    "NullAudioIO": ".audio_io",
    "SoundDeviceAudioIO": ".audio_io",
    "DesktopVoiceTurnRunner": ".runtime",
    "VoiceTurnResult": ".runtime",
    "infer_expression_from_text": ".runtime",
}

def __getattr__(name: str):
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    return getattr(module, name)
