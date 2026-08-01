import json

from nanobot.config.loader import load_config, save_config


def test_load_config_includes_voice_and_avatar_defaults(tmp_path) -> None:
    config = load_config(tmp_path / "missing.json")

    assert config.voice.enabled is False
    assert config.voice.stt.provider == "groq"
    assert config.voice.tts.provider == "edge_tts"
    assert config.voice.capture.command_template == []
    assert config.voice.capture.format == "wav"
    assert config.voice.playback.backend == "command"
    assert config.voice.playback.command_template[-1] == "{file}"
    assert config.avatar.enabled is False
    assert config.avatar.runtime == "vtube_studio"
    assert config.avatar.port == 8001


def test_load_config_migrates_legacy_flat_voice_provider_keys(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "voice": {
                    "enabled": True,
                    "sttProvider": "funasr",
                    "ttsProvider": "azure_tts",
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.voice.enabled is True
    assert config.voice.stt.provider == "funasr"
    assert config.voice.tts.provider == "azure_tts"


def test_save_config_writes_voice_and_avatar_structure(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = load_config(config_path)
    config.voice.enabled = True
    config.voice.stt.provider = "groq"
    config.voice.tts.provider = "edge_tts"
    config.avatar.enabled = True
    config.avatar.runtime = "none"
    save_config(config, config_path)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["voice"]["enabled"] is True
    assert saved["voice"]["stt"]["provider"] == "groq"
    assert saved["voice"]["tts"]["provider"] == "edge_tts"
    assert saved["avatar"]["enabled"] is True
    assert saved["avatar"]["runtime"] == "none"
