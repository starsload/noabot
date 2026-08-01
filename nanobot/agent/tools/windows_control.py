from __future__ import annotations

import importlib.util
import json
import mimetypes
import platform
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.utils.helpers import build_image_content_blocks, detect_image_mime


class WindowsControlTool(Tool):
    """Thin framework wrapper around the workspace windows-automation skill."""

    _READ_ONLY_ACTIONS = frozenset(
        {
            "capabilities",
            "inspect_screen",
            "screenshot",
            "find_text",
            "locate_template",
            "recent_events",
        }
    )

    def __init__(
        self,
        workspace: Path,
        platform_name: str | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self._platform_name = platform_name or platform.system()
        self._module: ModuleType | None = None
        self._automation: Any | None = None

    @classmethod
    def _module_path(cls, workspace: Path) -> Path:
        return Path(workspace).resolve() / "skills" / "windows-automation" / "__init__.py"

    @classmethod
    def is_available(cls, workspace: Path, platform_name: str | None = None) -> bool:
        return (
            (platform_name or platform.system()) == "Windows"
            and cls._module_path(workspace).exists()
        )

    @property
    def name(self) -> str:
        return "windows_control"

    @property
    def description(self) -> str:
        return (
            "Observe and control the Windows desktop through the workspace windows-automation skill. "
            "Use inspect_screen first to let the model see the current UI, then use OCR/template lookup "
            "and explicit confirmed actions for input."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        region_schema = {
            "type": "object",
            "properties": {
                "left": {"type": "integer"},
                "top": {"type": "integer"},
                "width": {"type": "integer", "minimum": 1},
                "height": {"type": "integer", "minimum": 1},
            },
            "required": ["left", "top", "width", "height"],
        }
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "capabilities",
                        "inspect_screen",
                        "screenshot",
                        "move_mouse",
                        "click",
                        "drag_to",
                        "press_key",
                        "hotkey",
                        "type_text",
                        "locate_template",
                        "click_template",
                        "find_text",
                        "click_text",
                        "run_actions",
                        "recent_events",
                    ],
                    "description": "Desktop control action to perform",
                },
                "x": {"type": "integer", "description": "X coordinate in screen pixels"},
                "y": {"type": "integer", "description": "Y coordinate in screen pixels"},
                "duration": {"type": "number", "minimum": 0},
                "button": {"type": "string", "enum": ["left", "middle", "right"]},
                "clicks": {"type": "integer", "minimum": 1, "maximum": 10},
                "interval": {"type": "number", "minimum": 0},
                "key": {"type": "string"},
                "keys": {"type": "array", "items": {"type": "string"}},
                "modifiers": {"type": "array", "items": {"type": "string"}},
                "text": {"type": "string"},
                "query": {"type": "string"},
                "template_path": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "grayscale": {"type": "boolean"},
                "region": region_schema,
                "output_path": {"type": "string"},
                "lang": {"type": "string"},
                "min_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                "match_index": {"type": "integer", "minimum": 0},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "duration": {"type": "number"},
                            "button": {"type": "string"},
                            "clicks": {"type": "integer"},
                            "interval": {"type": "number"},
                            "key": {"type": "string"},
                            "keys": {"type": "array", "items": {"type": "string"}},
                            "modifiers": {"type": "array", "items": {"type": "string"}},
                            "text": {"type": "string"},
                            "query": {"type": "string"},
                            "lang": {"type": "string"},
                            "min_confidence": {"type": "integer"},
                            "match_index": {"type": "integer"},
                            "output_path": {"type": "string"},
                            "region": region_schema,
                            "confirmed": {"type": "boolean"},
                        },
                        "required": ["action"],
                    },
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "confirmed": {"type": "boolean"},
                "dry_run": {"type": "boolean"},
                "require_confirmation": {"type": "boolean"},
                "failsafe": {"type": "boolean"},
                "max_clicks_per_second": {"type": "integer", "minimum": 1, "maximum": 50},
                "min_action_interval": {"type": "number", "minimum": 0},
                "tesseract_cmd": {"type": "string"},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        x: int | None = None,
        y: int | None = None,
        duration: float = 0.2,
        button: str = "left",
        clicks: int = 1,
        interval: float | None = None,
        key: str | None = None,
        keys: list[str] | None = None,
        modifiers: list[str] | None = None,
        text: str | None = None,
        query: str | None = None,
        template_path: str | None = None,
        confidence: float = 0.8,
        grayscale: bool = True,
        region: dict[str, Any] | None = None,
        output_path: str | None = None,
        lang: str = "chi_sim+eng",
        min_confidence: int = 50,
        match_index: int = 0,
        actions: list[dict[str, Any]] | None = None,
        limit: int = 20,
        confirmed: bool = False,
        dry_run: bool | None = None,
        require_confirmation: bool | None = None,
        failsafe: bool | None = None,
        max_clicks_per_second: int | None = None,
        min_action_interval: float | None = None,
        tesseract_cmd: str | None = None,
        **kwargs: Any,
    ) -> Any:
        if self._platform_name != "Windows":
            return "Error: windows_control is only available on Windows hosts."

        automation = self._get_automation()
        readonly = action in self._READ_ONLY_ACTIONS
        effective_dry_run = bool(dry_run) if dry_run is not None else (not readonly)
        effective_require_confirmation = (
            require_confirmation if require_confirmation is not None else (not readonly)
        )

        overrides = {
            "dry_run": effective_dry_run,
            "require_confirmation": effective_require_confirmation,
            "failsafe": failsafe,
            "max_clicks_per_second": max_clicks_per_second,
            "min_action_interval": min_action_interval,
        }
        if tesseract_cmd and getattr(automation, "pytesseract", None) is not None:
            automation.pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        with automation.temporary_config(**overrides):
            if action == "capabilities":
                return self._json(automation.capabilities())
            if action == "inspect_screen":
                inspected = automation.inspect_screen(output_path=output_path, region=region)
                return self._image_result(Path(inspected["path"]), label_prefix="Desktop inspection")
            if action == "screenshot":
                path = automation.screenshot(output_path=output_path, region=region, confirmed=True)
                return self._json({"path": path, "region": region or {}, "dry_run": effective_dry_run})
            if action == "move_mouse":
                return self._json(automation.move_mouse(x=x, y=y, duration=duration, confirmed=confirmed))
            if action == "click":
                return self._json(
                    automation.click(
                        x=x,
                        y=y,
                        button=button,
                        clicks=clicks,
                        interval=0.0 if interval is None else interval,
                        confirmed=confirmed,
                    )
                )
            if action == "drag_to":
                return self._json(
                    automation.drag_to(
                        x=self._require_value(x, "x"),
                        y=self._require_value(y, "y"),
                        duration=duration,
                        button=button,
                        confirmed=confirmed,
                    )
                )
            if action == "press_key":
                return self._json(
                    automation.press_key(
                        key=self._require_text(key, "key"),
                        modifiers=modifiers,
                        confirmed=confirmed,
                    )
                )
            if action == "hotkey":
                if not keys:
                    raise ValueError("keys is required for hotkey")
                return self._json(automation.hotkey(*keys, confirmed=confirmed))
            if action == "type_text":
                return self._json(
                    automation.type_text(
                        text=self._require_text(text, "text"),
                        interval=0.02 if interval is None else interval,
                        confirmed=confirmed,
                    )
                )
            if action == "locate_template":
                return self._json(
                    automation.locate_template(
                        template_path=self._require_text(template_path, "template_path"),
                        confidence=confidence,
                        region=region,
                        grayscale=grayscale,
                    )
                )
            if action == "click_template":
                return self._json(
                    automation.click_template(
                        template_path=self._require_text(template_path, "template_path"),
                        confidence=confidence,
                        region=region,
                        button=button,
                        confirmed=confirmed,
                    )
                )
            if action == "find_text":
                return self._json(
                    automation.find_text(
                        query=query or text,
                        region=region,
                        lang=lang,
                        min_confidence=min_confidence,
                    )
                )
            if action == "click_text":
                return self._json(
                    automation.click_text(
                        query=self._require_text(query or text, "query"),
                        region=region,
                        lang=lang,
                        min_confidence=min_confidence,
                        match_index=match_index,
                        button=button,
                        confirmed=confirmed,
                    )
                )
            if action == "run_actions":
                if not actions:
                    raise ValueError("actions is required for run_actions")
                return self._json(automation.run_actions(actions=actions, confirmed=confirmed))
            if action == "recent_events":
                return self._json(automation.recent_events(limit=limit))

        raise ValueError(f"Unsupported windows_control action: {action}")

    def _get_automation(self) -> Any:
        if self._automation is not None:
            return self._automation

        module = self._load_workspace_module()
        action_guard_cls = getattr(module, "ActionGuard", None)
        safety_config_cls = getattr(module, "SafetyConfig", None)
        automation_cls = getattr(module, "WindowsAutomation", None)
        if not action_guard_cls or not safety_config_cls or not automation_cls:
            raise RuntimeError(
                "workspace windows-automation skill must export ActionGuard, SafetyConfig, and WindowsAutomation"
            )

        log_path = self.workspace / "runtime" / "windows_control" / "actions.jsonl"
        guard = action_guard_cls(
            safety_config_cls(
                dry_run=True,
                require_confirmation=True,
                log_path=log_path,
            )
        )
        self._automation = automation_cls(workspace_root=self.workspace, safety=guard)
        return self._automation

    def _load_workspace_module(self) -> ModuleType:
        if self._module is not None:
            return self._module

        module_path = self._module_path(self.workspace)
        if not module_path.exists():
            raise FileNotFoundError(
                f"Workspace windows-automation skill not found: {module_path}"
            )

        module_name = f"_nanobot_workspace_windows_automation_{abs(hash(str(module_path)))}"
        if module_name in sys.modules:
            module = sys.modules[module_name]
            if isinstance(module, ModuleType):
                self._module = module
                return module

        spec = importlib.util.spec_from_file_location(
            module_name,
            module_path,
            submodule_search_locations=[str(module_path.parent)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Failed to load workspace windows-automation module from {module_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        self._module = module
        return module

    @staticmethod
    def _require_value(value: int | None, name: str) -> int:
        if value is None:
            raise ValueError(f"{name} is required")
        return int(value)

    @staticmethod
    def _require_text(value: str | None, name: str) -> str:
        if not value:
            raise ValueError(f"{name} is required")
        return str(value)

    @staticmethod
    def _json(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _image_result(path: Path, *, label_prefix: str) -> list[dict[str, Any]]:
        raw = path.read_bytes()
        mime = detect_image_mime(raw) or mimetypes.guess_type(str(path))[0] or "image/png"
        label = f"({label_prefix}: {path})"
        return build_image_content_blocks(raw, mime, str(path), label)
