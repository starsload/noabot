"""Configuration loading utilities."""

import html
import json
from pathlib import Path
from typing import Any, cast, overload

from pydantic import BaseModel, ValidationError
from pydantic_settings import SettingsError

from nanobot.config.errors import ConfigIssue, ConfigLoadError, validation_issues
from nanobot.config.schema import (
    Config,
    _resolve_tool_config_refs,  # pyright: ignore[reportPrivateUsage]
)
from nanobot.utils.helpers import _write_text_atomic  # pyright: ignore[reportPrivateUsage]

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None
_schema_refs_ready = False


def _as_config_object(value: object) -> dict[str, Any] | None:
    """Narrow an untrusted JSON configuration value to an object."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """Get the configuration file path."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".nanobot" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    global _schema_refs_ready
    if not _schema_refs_ready:
        _resolve_tool_config_refs()
        _schema_refs_ready = True

    path = config_path or get_config_path()

    if path.exists():
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            data = _migrate_config(data)
            return Config.model_validate(data)
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as e:
            logger.warning(f"Failed to load config from {path}: {e}")
            logger.warning("Using default configuration.")

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(mode="json", by_alias=True)
    # OAuth credentials live in dedicated token stores. Persist only the
    # non-credential request settings consumed by these provider backends.
    for alias, provider in (
        ("openaiCodex", config.providers.openai_codex),
        ("xaiGrok", config.providers.xai_grok),
    ):
        settings = provider.model_dump(
            mode="json",
            by_alias=True,
            include={"proxy", "extra_body"},
            exclude_none=True,
        )
        if settings:
            data.setdefault("providers", {})[alias] = settings

    # Temp + replace so a crash mid-write cannot leave a truncated config.json.
    _write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))


def _normalize_mcp_arg(value: object) -> object:
    """Normalize HTML-escaped or accidentally quoted MCP command arguments."""
    if not isinstance(value, str):
        return value

    normalized = html.unescape(value).strip()
    if normalized.startswith('"') and normalized.count('"') == 1:
        normalized = normalized[1:]
    elif normalized.endswith('"') and normalized.count('"') == 1:
        normalized = normalized[:-1]
    elif len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    return normalized


def _migrate_config(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate old config formats to current."""
    agents = data.get("agents", {})
    defaults = agents.get("defaults", {})

    legacy_memory_window_present = (
        "memoryWindow" in defaults or "memory_window" in defaults
    )
    context_window_present = (
        "contextWindowTokens" in defaults or "context_window_tokens" in defaults
    )
    if legacy_memory_window_present and not context_window_present:
        defaults["shouldWarnDeprecatedMemoryWindow"] = True

    defaults.pop("memoryWindow", None)
    defaults.pop("memory_window", None)

    # Move tools.exec.restrictToWorkspace -> tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    channels = data.get("channels", {})
    if "qqPersonal" in channels and "qq_personal" not in channels:
        channels["qq_personal"] = channels.pop("qqPersonal")

    mcp_servers = tools.get("mcpServers", {})
    for server_cfg in mcp_servers.values():
        if isinstance(server_cfg, dict) and isinstance(server_cfg.get("args"), list):
            server_cfg["args"] = [_normalize_mcp_arg(arg) for arg in server_cfg["args"]]

    # Migrate legacy voice flat provider keys:
    # {
    #   "voice": {"sttProvider": "...", "ttsProvider": "..."}
    # }
    # -> {
    #   "voice": {"stt": {"provider": "..."}, "tts": {"provider": "..."}}
    # }
    voice = data.get("voice")
    if isinstance(voice, dict):
        stt_provider = voice.pop("sttProvider", voice.pop("stt_provider", None))
        tts_provider = voice.pop("ttsProvider", voice.pop("tts_provider", None))
        if stt_provider:
            stt_cfg = voice.get("stt", {})
            if isinstance(stt_cfg, dict) and "provider" not in stt_cfg:
                stt_cfg["provider"] = stt_provider
                voice["stt"] = stt_cfg
        if tts_provider:
            tts_cfg = voice.get("tts", {})
            if isinstance(tts_cfg, dict) and "provider" not in tts_cfg:
                tts_cfg["provider"] = tts_provider
                voice["tts"] = tts_cfg
    return data


def _sentence(message: str) -> str:
    message = message.strip()
    if message and message[-1] not in ".!?":
        message += "."
    return message
