"""VTube Studio runtime adapter with optional pyvts dependency."""

from __future__ import annotations

import inspect
from importlib import import_module
from typing import Any, Callable

from nanobot.avatar.base import AvatarIntent, BaseAvatarRuntime


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class VTubeStudioRuntime(BaseAvatarRuntime):
    """Avatar runtime adapter for VTube Studio via pyvts."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8001,
        plugin_name: str = "nanobot-desktop-avatar",
        plugin_developer: str = "nanobot",
        token_path: str = "./token.txt",
        speaking_parameter: str = "MouthOpen",
        speaking_value_on: float = 1.0,
        speaking_value_off: float = 0.0,
        expression_hotkeys: dict[str, str] | None = None,
        motion_hotkeys: dict[str, str] | None = None,
        module_loader: Callable[[str], Any] = import_module,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.plugin_name = plugin_name
        self.plugin_developer = plugin_developer
        self.token_path = token_path
        self.speaking_parameter = speaking_parameter
        self.speaking_value_on = speaking_value_on
        self.speaking_value_off = speaking_value_off
        self.expression_hotkeys = dict(expression_hotkeys or {})
        self.motion_hotkeys = dict(motion_hotkeys or {})
        self._connected = False
        self._client: Any | None = None

        if client_factory is not None:
            self._client_factory = client_factory
            return

        try:
            module = module_loader("pyvts")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pyvts is required for VTubeStudioRuntime. Install with: pip install pyvts"
            ) from exc

        self._client_factory = self._build_default_client_factory(module)

    @property
    def name(self) -> str:
        return "vtube_studio"

    @property
    def is_connected(self) -> bool:
        return self._connected

    @staticmethod
    def _build_default_client_factory(module: Any) -> Callable[..., Any]:
        vts_cls = getattr(module, "vts", None) or getattr(module, "VTS", None)
        if vts_cls is None:
            raise RuntimeError("pyvts module does not expose vts/VTS client constructor")

        def _factory(
            *,
            host: str,
            port: int,
            plugin_name: str,
            plugin_developer: str,
            token_path: str,
        ) -> Any:
            plugin_info = {
                "plugin_name": plugin_name,
                "developer": plugin_developer,
                "authentication_token_path": token_path,
            }
            vts_api_info = {
                "host": host,
                "port": port,
                "name": "VTubeStudioPublicAPI",
                "version": "1.0",
            }
            try:
                return vts_cls(plugin_info=plugin_info, vts_api_info=vts_api_info)
            except TypeError:
                # Fallback for pyvts variants with a simpler constructor.
                return vts_cls(plugin_info=plugin_info)

        return _factory

    async def connect(self) -> None:
        if self._connected:
            return
        self._client = self._client_factory(
            host=self.host,
            port=self.port,
            plugin_name=self.plugin_name,
            plugin_developer=self.plugin_developer,
            token_path=self.token_path,
        )
        await _maybe_await(getattr(self._client, "connect")())

        token_fn = getattr(self._client, "request_authenticate_token", None)
        auth_fn = getattr(self._client, "request_authenticate", None)
        if callable(token_fn) and callable(auth_fn):
            await _maybe_await(token_fn())
            await _maybe_await(auth_fn())
        self._connected = True

    async def disconnect(self) -> None:
        if not self._connected or self._client is None:
            self._connected = False
            return

        close_fn = getattr(self._client, "close", None)
        if callable(close_fn):
            await _maybe_await(close_fn())
        else:
            disconnect_fn = getattr(self._client, "disconnect", None)
            if callable(disconnect_fn):
                await _maybe_await(disconnect_fn())
        self._connected = False

    async def apply_intent(self, intent: AvatarIntent) -> None:
        if not self._connected or self._client is None:
            return

        client = self._client
        vts_req = getattr(client, "vts_request", None)
        request_fn = getattr(client, "request", None)
        if not callable(request_fn) or vts_req is None:
            return

        # Expression / motion are best-effort mapped to VTS hotkeys.
        trigger_hotkey_fn = getattr(vts_req, "requestTriggerHotKey", None)
        if callable(trigger_hotkey_fn):
            if intent.expression:
                expression_hotkey = self.expression_hotkeys.get(intent.expression, intent.expression)
                await _maybe_await(request_fn(trigger_hotkey_fn(expression_hotkey)))
            if intent.motion:
                motion_hotkey = self.motion_hotkeys.get(intent.motion, intent.motion)
                await _maybe_await(request_fn(trigger_hotkey_fn(motion_hotkey)))

        # speaking -> mouth-open parameter via InjectParameterDataRequest.
        if intent.speaking is not None:
            value = self.speaking_value_on if intent.speaking else self.speaking_value_off
            set_param_fn = getattr(vts_req, "requestSetParameterValue", None)
            if callable(set_param_fn):
                await _maybe_await(request_fn(set_param_fn(self.speaking_parameter, value)))
