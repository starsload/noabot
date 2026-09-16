"""UTF-8 BOM tolerance for config.json reads (noabot).

PowerShell/editor round-trips on Windows keep re-adding a BOM to
~/.nanobot/config.json; the strict utf-8 reads made the whole gateway fail
to boot with "Unexpected UTF-8 BOM" (hit twice: 2026-08-31, 2026-09-16).
Readers must accept it; writers must keep producing clean UTF-8.
"""

from __future__ import annotations

import json

from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.optional_features import read_config_data


def _with_bom(path, config_dict: dict) -> None:
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(config_dict).encode("utf-8"))


def test_load_config_accepts_bom(tmp_path) -> None:
    path = tmp_path / "config.json"
    _with_bom(path, {"agents": {"defaults": {"workspace": str(tmp_path)}}})

    config = load_config(path)

    assert str(config.agents.defaults.workspace) == str(tmp_path)


def test_read_config_data_accepts_bom(tmp_path) -> None:
    path = tmp_path / "config.json"
    _with_bom(path, {"channels": {"discord": {"enabled": True}}})

    data = read_config_data(path)

    assert data["channels"]["discord"]["enabled"] is True


def test_save_config_writes_no_bom(tmp_path) -> None:
    path = tmp_path / "config.json"

    save_config(Config(), path)

    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
