"""Configuration loading utilities."""

import html
import json
from pathlib import Path

import pydantic
from loguru import logger

from nanobot.config.schema import Config

# Global variable to store current config path (for multi-instance support)
_current_config_path: Path | None = None


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
    path = config_path or get_config_path()

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
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

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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


def _migrate_config(data: dict) -> dict:
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

    mcp_servers = tools.get("mcpServers", {})
    for server_cfg in mcp_servers.values():
        if isinstance(server_cfg, dict) and isinstance(server_cfg.get("args"), list):
            server_cfg["args"] = [_normalize_mcp_arg(arg) for arg in server_cfg["args"]]
    return data
