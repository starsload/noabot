from pathlib import Path

import pytest

from nanobot.providers.stt.groq_stt import GroqSTTProvider
from nanobot.providers.tts.edge_tts import EdgeTTSProvider


@pytest.mark.asyncio
async def test_groq_stt_provider_wraps_transcription(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_transcribe(self, file_path):  # noqa: ANN001
        assert str(file_path).endswith("sample.wav")
        return "hello world"

    monkeypatch.setattr(
        "nanobot.providers.transcription.GroqTranscriptionProvider.transcribe",
        _fake_transcribe,
    )

    provider = GroqSTTProvider(api_key="test")
    result = await provider.transcribe_file("sample.wav")

    assert provider.name == "groq"
    assert result.text == "hello world"
    assert result.raw == {"provider": "groq"}


@pytest.mark.asyncio
async def test_edge_tts_provider_synthesizes_with_injected_module(tmp_path: Path) -> None:
    class _FakeCommunicate:
        def __init__(self, text: str, voice: str, rate: str, volume: str, pitch: str) -> None:
            self.text = text
            self.voice = voice
            self.rate = rate
            self.volume = volume
            self.pitch = pitch

        async def save(self, output_path: str) -> None:
            Path(output_path).write_bytes(b"fake-audio")

    class _FakeEdgeTTSModule:
        Communicate = _FakeCommunicate

    provider = EdgeTTSProvider(
        default_voice="zh-CN-XiaoxiaoNeural",
        module_loader=lambda _: _FakeEdgeTTSModule,
    )
    out = tmp_path / "speech.mp3"
    result = await provider.synthesize_to_file("hello", out)

    assert provider.name == "edge_tts"
    assert out.exists()
    assert out.read_bytes() == b"fake-audio"
    assert result.path == out.resolve()
    assert result.audio_format == "mp3"
    assert result.raw == {"provider": "edge_tts", "voice": "zh-CN-XiaoxiaoNeural"}


def test_edge_tts_provider_raises_helpful_error_when_dependency_missing() -> None:
    with pytest.raises(RuntimeError) as exc:
        EdgeTTSProvider(module_loader=lambda _: (_ for _ in ()).throw(ModuleNotFoundError("edge_tts")))

    assert "pip install edge-tts" in str(exc.value)

