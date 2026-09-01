"""Dedicated tool for the workspace noah-local-painter integration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage


class NoahLocalPainterTool(Tool):
    _plugin_discoverable = False  # Requires workspace + bus callback; registered manually
    """Safe wrapper around the workspace noah-local-painter helper."""

    _DEFAULT_PRESET = "presets/noah_halfbody.json"

    def __init__(
        self,
        workspace: Path,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
    ) -> None:
        self.workspace = workspace
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._default_message_id = default_message_id
        self._sent_in_turn = False

    @classmethod
    def is_available(cls, workspace: Path) -> bool:
        """Return whether the workspace painter wrapper exists."""
        return cls._module_path(workspace).exists()

    @staticmethod
    def _module_path(workspace: Path) -> Path:
        return workspace / "libs" / "noah_local_painter" / "__init__.py"

    def set_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Set the current chat target for optional image delivery."""
        self._default_channel = channel
        self._default_chat_id = chat_id
        self._default_message_id = message_id

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set outbound sender callback."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn delivery tracking."""
        self._sent_in_turn = False

    @property
    def name(self) -> str:
        return "noah_local_painter"

    @property
    def description(self) -> str:
        return (
            "Generate images through the workspace noah-local-painter integration. "
            "Supports health_check and txt2img generate. "
            "When deliver=true, generated images are sent back to the current chat directly."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["health_check", "generate"],
                    "description": "Painter action to run",
                },
                "preset": {
                    "type": "string",
                    "description": "Preset JSON path relative to the painter project",
                },
                "prompt": {
                    "type": "string",
                    "description": "Prompt text appended to the preset prompt",
                    "maxLength": 4000,
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Optional negative prompt override",
                    "maxLength": 4000,
                },
                "count": {
                    "type": "integer",
                    "description": "How many images to generate",
                    "minimum": 1,
                    "maximum": 4,
                },
                "width": {
                    "type": "integer",
                    "description": "Output width in pixels",
                    "minimum": 64,
                    "maximum": 2048,
                },
                "height": {
                    "type": "integer",
                    "description": "Output height in pixels",
                    "minimum": 64,
                    "maximum": 2048,
                },
                "steps": {
                    "type": "integer",
                    "description": "Sampling steps",
                    "minimum": 1,
                    "maximum": 50,
                },
                "seed": {
                    "type": "integer",
                    "description": "Optional deterministic seed",
                },
                "model": {
                    "type": "string",
                    "description": "Optional checkpoint/model override",
                    "maxLength": 300,
                },
                "api_base_url": {
                    "type": "string",
                    "description": "Optional Forge/A1111 API base URL override",
                    "maxLength": 500,
                },
                "config": {
                    "type": "string",
                    "description": "Optional painter config path",
                    "maxLength": 500,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Build the request without generating an image",
                },
                "auto_start": {
                    "type": "boolean",
                    "description": "Auto-start Forge if it is offline",
                },
                "wait_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait for Forge to become ready",
                    "minimum": 1,
                    "maximum": 600,
                },
                "deliver": {
                    "type": "boolean",
                    "description": "Send generated images to the current chat automatically",
                },
                "delivery_text": {
                    "type": "string",
                    "description": "Optional caption when delivering generated images",
                    "maxLength": 500,
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        preset: str = _DEFAULT_PRESET,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        count: int | None = None,
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        seed: int | None = None,
        model: str | None = None,
        api_base_url: str | None = None,
        config: str | None = None,
        dry_run: bool = False,
        auto_start: bool = True,
        wait_timeout: int = 180,
        deliver: bool = True,
        delivery_text: str | None = None,
        **kwargs: Any,
    ) -> str:
        painter = await asyncio.to_thread(self._create_painter)

        if action == "health_check":
            response = await asyncio.to_thread(
                painter.health_check,
                api_base_url=api_base_url,
                config=config,
                auto_start=auto_start,
                wait_timeout=wait_timeout,
            )
            return self._serialize_response(response, delivered=False)

        response = await asyncio.to_thread(
            painter.generate,
            preset=preset,
            prompt=prompt,
            negative_prompt=negative_prompt,
            count=count,
            width=width,
            height=height,
            steps=steps,
            seed=seed,
            model=model,
            api_base_url=api_base_url,
            config=config,
            dry_run=dry_run,
            auto_start=auto_start,
            wait_timeout=wait_timeout,
        )

        delivered = False
        image_paths = self._coerce_list(getattr(response, "image_paths", []))
        if deliver and image_paths:
            delivered = await self._deliver_generated_images(
                image_paths,
                delivery_text or self._default_delivery_text(len(image_paths)),
            )

        return self._serialize_response(response, delivered=delivered)

    def _create_painter(self) -> Any:
        module = self._load_workspace_module()
        painter_cls = getattr(module, "NoahLocalPainter", None)
        if painter_cls is None:
            raise RuntimeError("Workspace module does not export NoahLocalPainter")
        return painter_cls()

    def _load_workspace_module(self) -> Any:
        module_path = self._module_path(self.workspace)
        if not module_path.exists():
            raise FileNotFoundError(f"Workspace painter wrapper not found: {module_path}")

        module_name = f"_nanobot_workspace_noah_local_painter_{abs(hash(str(module_path)))}"
        if module_name in sys.modules:
            return sys.modules[module_name]

        spec = importlib.util.spec_from_file_location(
            module_name,
            module_path,
            submodule_search_locations=[str(module_path.parent)],
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Failed to load workspace painter module from {module_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    async def _deliver_generated_images(self, image_paths: list[str], content: str) -> bool:
        channel = self._default_channel
        chat_id = self._default_chat_id
        if not channel or not chat_id:
            raise RuntimeError("No target channel/chat configured for image delivery")
        if not self._send_callback:
            raise RuntimeError("Image delivery is not configured")

        metadata = {"message_id": self._default_message_id} if self._default_message_id else {}
        await self._send_callback(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=content,
                media=image_paths,
                metadata=metadata,
            )
        )
        self._sent_in_turn = True
        return True

    @staticmethod
    def _default_delivery_text(image_count: int) -> str:
        suffix = "" if image_count == 1 else "s"
        return f"Generated {image_count} image{suffix}."

    @staticmethod
    def _coerce_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def _serialize_response(self, response: Any, *, delivered: bool) -> str:
        payload = {
            "ok": bool(getattr(response, "ok", True)),
            "mode": self._string_attr(response, "mode"),
            "message": self._string_attr(response, "message"),
            "current_model": self._string_attr(response, "current_model"),
            "run_dir": self._string_attr(response, "run_dir"),
            "metadata_path": self._string_attr(response, "metadata_path"),
            "image_paths": self._coerce_list(getattr(response, "image_paths", [])),
            "payload": getattr(response, "payload", {}),
            "delivered": delivered,
        }
        cleaned = {
            key: value
            for key, value in payload.items()
            if value not in ("", [], None) and not (key == "payload" and value == {})
        }
        return json.dumps(cleaned, ensure_ascii=False)

    @staticmethod
    def _string_attr(response: Any, name: str) -> str:
        value = getattr(response, name, "")
        return value if isinstance(value, str) else ""
