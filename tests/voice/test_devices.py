from types import SimpleNamespace

import pytest

from nanobot.voice.devices import (
    AudioDeviceInfo,
    list_audio_devices,
    parse_device_selector,
    resolve_audio_device,
)


def test_list_audio_devices_filters_input_and_output() -> None:
    class _FakeSoundDevice:
        default = SimpleNamespace(device=(0, 1))

        @staticmethod
        def query_devices():
            return [
                {"name": "Mic 1", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 16000},
                {"name": "Speaker 1", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000},
                {"name": "Combo", "max_input_channels": 1, "max_output_channels": 2, "default_samplerate": 44100},
            ]

    def _loader(name: str):
        if name == "sounddevice":
            return _FakeSoundDevice
        raise ModuleNotFoundError(name)

    all_devices = list_audio_devices(module_loader=_loader)
    input_devices = list_audio_devices(input_only=True, module_loader=_loader)
    output_devices = list_audio_devices(output_only=True, module_loader=_loader)

    assert all_devices == [
        AudioDeviceInfo(index=0, name="Mic 1", max_input_channels=2, max_output_channels=0, default_samplerate=16000, is_default_input=True, is_default_output=False),
        AudioDeviceInfo(index=1, name="Speaker 1", max_input_channels=0, max_output_channels=2, default_samplerate=48000, is_default_input=False, is_default_output=True),
        AudioDeviceInfo(index=2, name="Combo", max_input_channels=1, max_output_channels=2, default_samplerate=44100),
    ]
    assert [d.name for d in input_devices] == ["Mic 1", "Combo"]
    assert [d.name for d in output_devices] == ["Speaker 1", "Combo"]


def test_list_audio_devices_requires_sounddevice_dependency() -> None:
    with pytest.raises(RuntimeError) as exc:
        list_audio_devices(module_loader=lambda _: (_ for _ in ()).throw(ModuleNotFoundError("sounddevice")))

    assert "pip install sounddevice" in str(exc.value)


def test_parse_device_selector_supports_empty_numeric_and_named_values() -> None:
    assert parse_device_selector(None) is None
    assert parse_device_selector("") is None
    assert parse_device_selector("  ") is None
    assert parse_device_selector("default") is None
    assert parse_device_selector("auto") is None
    assert parse_device_selector("3") == 3
    assert parse_device_selector("Built-in Mic") == "Built-in Mic"


def test_resolve_audio_device_supports_default_exact_and_fuzzy_match() -> None:
    class _FakeSoundDevice:
        default = SimpleNamespace(device=(2, 1))

        @staticmethod
        def query_devices():
            return [
                {"name": "Mic 1"},
                {"name": "Built-in Output"},
                {"name": "USB Mic Pro"},
            ]

    def _loader(name: str):
        if name == "sounddevice":
            return _FakeSoundDevice
        raise ModuleNotFoundError(name)

    assert resolve_audio_device(None, kind="input", module_loader=_loader) == 2
    assert resolve_audio_device("Built-in Output", kind="output", module_loader=_loader) == 1
    assert resolve_audio_device("usb mic", kind="input", module_loader=_loader) == 2


def test_resolve_audio_device_raises_on_unknown_selector() -> None:
    class _FakeSoundDevice:
        default = SimpleNamespace(device=(0, 0))

        @staticmethod
        def query_devices():
            return [{"name": "Mic 1"}]

    def _loader(name: str):
        if name == "sounddevice":
            return _FakeSoundDevice
        raise ModuleNotFoundError(name)

    with pytest.raises(RuntimeError):
        resolve_audio_device("missing", kind="input", module_loader=_loader)
