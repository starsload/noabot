import json

import pytest

from nanobot.config.loader import load_config, save_config


def test_load_config_keeps_max_tokens_and_ignores_legacy_memory_window(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 1234,
                        "memoryWindow": 42,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agents.defaults.max_tokens == 1234
    assert config.agents.defaults.context_window_tokens == 65_536
    assert config.agents.defaults.should_warn_deprecated_memory_window is True
    assert not hasattr(config.agents.defaults, "memory_window")


def test_load_config_does_not_warn_when_context_window_tokens_is_already_present(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "memoryWindow": 42,
                        "contextWindowTokens": 32_768,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agents.defaults.context_window_tokens == 32_768
    assert config.agents.defaults.should_warn_deprecated_memory_window is False


def test_load_config_normalizes_html_escaped_mcp_args(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "tools": {
                    "mcpServers": {
                        "chrome-devtools": {
                            "command": "npx",
                            "args": ["&quot;--autoConnect", "--channel=beta"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.tools.mcp_servers["chrome-devtools"].args == [
        "--autoConnect",
        "--channel=beta",
    ]


def test_save_config_writes_context_window_tokens_but_not_memory_window(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 2222,
                        "memoryWindow": 30,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    save_config(config, config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    defaults = saved["agents"]["defaults"]

    assert defaults["maxTokens"] == 2222
    assert defaults["contextWindowTokens"] == 200_000
    assert "memoryWindow" not in defaults
    assert "shouldWarnDeprecatedMemoryWindow" not in defaults


def test_onboard_does_not_crash_with_legacy_memory_window(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "maxTokens": 3333,
                        "memoryWindow": 50,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("nanobot.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("nanobot.cli.commands.get_workspace_path", lambda _workspace=None: workspace)

    from typer.testing import CliRunner

    from nanobot.cli.commands import app
    runner = CliRunner()
    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0


@pytest.mark.parametrize("field_name", ["maxMessages", "max_messages"])
def test_load_config_ignores_legacy_max_messages(tmp_path, field_name) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {field_name: 25, "maxTokens": 1234}}}),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.agents.defaults.max_tokens == 1234
    assert not hasattr(config.agents.defaults, "max_messages")


def test_save_config_drops_legacy_max_messages(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agents": {"defaults": {"maxMessages": 25}}}),
        encoding="utf-8",
    )

    config = load_config(config_path)
    save_config(config, config_path)
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert "maxMessages" not in saved["agents"]["defaults"]
    assert "max_messages" not in saved["agents"]["defaults"]


def test_onboard_refresh_backfills_missing_channel_fields(tmp_path, monkeypatch) -> None:
    from nanobot.channels.plugin import load_channel_package

    config_path = tmp_path / "config.json"
    workspace = tmp_path / "workspace"
    config_path.write_text(
        json.dumps(
            {
                "channels": {
                    "qq": {
                        "enabled": False,
                        "appId": "",
                        "secret": "",
                        "allowFrom": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("nanobot.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("nanobot.cli.commands.get_workspace_path", lambda _workspace=None: workspace)
    monkeypatch.setattr(
        "nanobot.channels.registry.discover_plugins",
        lambda: {"qq": load_channel_package("qq")},
    )
    monkeypatch.setattr(
        "nanobot.channels.registry.discover_all",
        lambda: pytest.fail("onboarding must not import channel runtimes"),
    )

    from typer.testing import CliRunner

    from nanobot.cli.commands import app
    runner = CliRunner()
    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["channels"]["qq"]["msgFormat"] == "plain"


def test_save_and_load_config_preserves_owner_ids(tmp_path) -> None:
    config_path = tmp_path / "config.json"

    config = load_config(config_path)
    config.agents.identity.owner_ids = ["telegram:123", "qq_personal:456"]
    save_config(config, config_path)

    reloaded = load_config(config_path)

    assert reloaded.agents.identity.owner_ids == ["telegram:123", "qq_personal:456"]


def test_load_config_migrates_q_personal_channel_key(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "channels": {
                    "qqPersonal": {
                        "enabled": True,
                        "wsUrl": "ws://127.0.0.1:3001",
                        "httpUrl": "http://127.0.0.1:3000",
                        "accessToken": "token",
                        "allowFrom": ["123"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    qq_personal = getattr(config.channels, "qq_personal", None)
    assert qq_personal is not None
    assert isinstance(qq_personal, dict)
    assert qq_personal["enabled"] is True
    assert qq_personal["wsUrl"] == "ws://127.0.0.1:3001"
