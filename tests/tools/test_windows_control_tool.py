from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.agent.tools.windows_control import WindowsControlTool


def _install_fake_windows_automation_skill(workspace: Path) -> None:
    skill_dir = workspace / "skills" / "windows-automation"
    skill_dir.mkdir(parents=True)
    (skill_dir / "__init__.py").write_text(
        """
class SafetyViolation(RuntimeError):
    pass

class SafetyConfig:
    def __init__(self, dry_run=True, require_confirmation=True, log_path=None, failsafe=True, max_clicks_per_second=10, min_action_interval=0.02):
        self.dry_run = dry_run
        self.require_confirmation = require_confirmation
        self.log_path = log_path
        self.failsafe = failsafe
        self.max_clicks_per_second = max_clicks_per_second
        self.min_action_interval = min_action_interval

class ActionGuard:
    def __init__(self, config):
        self.config = config
        self.events = []

    def recent_events(self, limit=50):
        return self.events[-limit:]

class WindowsAutomation:
    def __init__(self, workspace_root, safety):
        self.workspace_root = workspace_root
        self.safety = safety
        self.calls = []
        self.pytesseract = None

    def temporary_config(self, **overrides):
        automation = self
        class _Ctx:
            def __enter__(self_inner):
                for key, value in overrides.items():
                    if hasattr(automation.safety.config, key) and value is not None:
                        setattr(automation.safety.config, key, value)
                return automation
            def __exit__(self_inner, exc_type, exc, tb):
                return False
        return _Ctx()

    def capabilities(self):
        return {"dry_run": self.safety.config.dry_run, "failsafe": self.safety.config.failsafe}

    def inspect_screen(self, output_path=None, region=None):
        path = self.workspace_root / "runtime" / "windows_control" / "fake-screen.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            b"\\x89PNG\\r\\n\\x1a\\n"
            + b"fake"
        )
        return {"path": str(path), "region": region or {}}

    def screenshot(self, output_path=None, region=None, confirmed=True):
        return self.inspect_screen(output_path=output_path, region=region)["path"]

    def click(self, x=None, y=None, button="left", clicks=1, interval=0.0, confirmed=False):
        if not self.safety.config.dry_run and not confirmed and self.safety.config.require_confirmation:
            raise SafetyViolation("confirmation required")
        payload = {"x": x, "y": y, "button": button, "clicks": clicks, "interval": interval}
        status = "dry_run" if self.safety.config.dry_run else "ok"
        self.safety.events.append({"action": "click", "status": status, "payload": payload})
        self.calls.append(("click", x, y, button, clicks, interval))
        return payload

    def move_mouse(self, x, y, duration=0.2, confirmed=False):
        return {"x": x, "y": y, "duration": duration}

    def drag_to(self, x, y, duration=0.2, button="left", confirmed=False):
        return {"x": x, "y": y, "duration": duration, "button": button}

    def press_key(self, key, modifiers=None, confirmed=False):
        return {"key": key, "modifiers": modifiers or []}

    def hotkey(self, *keys, confirmed=False):
        return {"keys": list(keys)}

    def type_text(self, text, interval=0.02, confirmed=False):
        return {"text": text, "interval": interval}

    def locate_template(self, template_path, confidence=0.8, region=None, grayscale=True):
        return {"center_x": 30, "center_y": 30, "confidence": confidence}

    def click_template(self, template_path, confidence=0.8, region=None, button="left", confirmed=False):
        return {"center_x": 30, "center_y": 30, "confidence": confidence}

    def find_text(self, query=None, region=None, lang="chi_sim+eng", min_confidence=50):
        return [{"text": "Save", "center_x": 30, "center_y": 30, "confidence": 88}]

    def click_text(self, query, region=None, lang="chi_sim+eng", min_confidence=50, match_index=0, button="left", confirmed=False):
        if not self.safety.config.dry_run and not confirmed and self.safety.config.require_confirmation:
            raise SafetyViolation("confirmation required")
        self.calls.append(("click_text", query, button))
        return {"text": query, "center_x": 30, "center_y": 30, "confidence": 88}

    def run_actions(self, actions, confirmed=False):
        return [{"action": item["action"]} for item in actions]

    def recent_events(self, limit=50):
        return self.safety.events[-limit:]
""".strip(),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_inspect_screen_returns_multimodal_blocks(tmp_path: Path) -> None:
    _install_fake_windows_automation_skill(tmp_path)
    tool = WindowsControlTool(workspace=tmp_path)

    result = await tool.execute(action="inspect_screen")

    assert isinstance(result, list)
    assert result[0]["type"] == "image_url"
    assert result[0]["image_url"]["url"].startswith("data:image/")
    assert result[1]["type"] == "text"


@pytest.mark.asyncio
async def test_click_defaults_to_dry_run(tmp_path: Path) -> None:
    _install_fake_windows_automation_skill(tmp_path)
    tool = WindowsControlTool(workspace=tmp_path)

    raw = await tool.execute(action="click", x=10, y=20)
    payload = json.loads(raw)

    assert payload["x"] == 10
    assert payload["y"] == 20


@pytest.mark.asyncio
async def test_click_requires_confirmation_when_executing(tmp_path: Path) -> None:
    _install_fake_windows_automation_skill(tmp_path)
    tool = WindowsControlTool(workspace=tmp_path)

    with pytest.raises(RuntimeError):
        await tool.execute(action="click", x=10, y=20, dry_run=False)


@pytest.mark.asyncio
async def test_click_executes_when_confirmed(tmp_path: Path) -> None:
    _install_fake_windows_automation_skill(tmp_path)
    tool = WindowsControlTool(workspace=tmp_path)

    raw = await tool.execute(action="click", x=10, y=20, dry_run=False, confirmed=True)
    payload = json.loads(raw)

    assert payload["x"] == 10


@pytest.mark.asyncio
async def test_find_text_returns_matches(tmp_path: Path) -> None:
    _install_fake_windows_automation_skill(tmp_path)
    tool = WindowsControlTool(workspace=tmp_path)

    raw = await tool.execute(action="find_text", query="Save")
    matches = json.loads(raw)

    assert len(matches) == 1
    assert matches[0]["text"] == "Save"
    assert matches[0]["center_x"] == 30


@pytest.mark.asyncio
async def test_click_text_executes_after_match(tmp_path: Path) -> None:
    _install_fake_windows_automation_skill(tmp_path)
    tool = WindowsControlTool(workspace=tmp_path)

    raw = await tool.execute(
        action="click_text",
        query="Save",
        dry_run=False,
        confirmed=True,
    )
    match = json.loads(raw)

    assert match["text"] == "Save"


@pytest.mark.asyncio
async def test_recent_events_returns_history(tmp_path: Path) -> None:
    _install_fake_windows_automation_skill(tmp_path)
    tool = WindowsControlTool(workspace=tmp_path)

    await tool.execute(action="click", x=10, y=20)
    raw = await tool.execute(action="recent_events", limit=5)
    events = json.loads(raw)

    assert len(events) == 1
    assert events[0]["action"] == "click"
    assert events[0]["status"] == "dry_run"


@pytest.mark.asyncio
async def test_windows_control_rejects_non_windows_hosts(tmp_path: Path) -> None:
    _install_fake_windows_automation_skill(tmp_path)
    tool = WindowsControlTool(workspace=tmp_path, platform_name="Linux")

    result = await tool.execute(action="capabilities")

    assert result == "Error: windows_control is only available on Windows hosts."
