from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from nanobot.agent.tools import mcp as mcp_module
from nanobot.agent.tools.mcp import MCPToolWrapper, connect_mcp_servers
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import MCPServerConfig

_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def test_type_checking_only_mcp_annotations_are_deferred() -> None:
    assert mcp_mod._MCPWrapperBase.__annotations__["_session"] == "ClientSession"
    assert MCPToolWrapper.__init__.__annotations__["session"] == "ClientSession"
    assert MCPResourceWrapper.__init__.__annotations__["resource_def"] == "Resource"
    assert MCPPromptWrapper.__init__.__annotations__["prompt_def"] == "Prompt"
    assert connect_mcp_servers.__annotations__["mcp_servers"] == "dict[str, MCPServerConfig]"


class _FakeTextContent:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.fixture
def fake_mcp_runtime() -> dict[str, object | None]:
    return {"session": None}


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*_PROXY_ENV_VARS, "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fake_mcp_module(
    monkeypatch: pytest.MonkeyPatch, fake_mcp_runtime: dict[str, object | None]
) -> None:
    mod = ModuleType("mcp")
    mod.types = SimpleNamespace(TextContent=_FakeTextContent)

    class _FakeStdioServerParameters:
        def __init__(
            self,
            command: str,
            args: list[str],
            env: dict | None = None,
            cwd: str | None = None,
        ) -> None:
            self.command = command
            self.args = args
            self.env = env
            self.cwd = cwd

    class _FakeClientSession:
        def __init__(self, _read: object, _write: object) -> None:
            self._session = fake_mcp_runtime["session"]

        async def __aenter__(self) -> object:
            return self._session

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    @asynccontextmanager
    async def _fake_stdio_client(_params: object):
        yield object(), object()

    @asynccontextmanager
    async def _fake_sse_client(_url: str, httpx_client_factory=None):
        yield object(), object()

    @asynccontextmanager
    async def _fake_streamable_http_client(_url: str, http_client=None):
        yield object(), object(), object()

    mod.ClientSession = _FakeClientSession
    mod.StdioServerParameters = _FakeStdioServerParameters
    monkeypatch.setitem(sys.modules, "mcp", mod)

    client_mod = ModuleType("mcp.client")
    stdio_mod = ModuleType("mcp.client.stdio")
    stdio_mod.stdio_client = _fake_stdio_client
    sse_mod = ModuleType("mcp.client.sse")
    sse_mod.sse_client = _fake_sse_client
    streamable_http_mod = ModuleType("mcp.client.streamable_http")
    streamable_http_mod.streamable_http_client = _fake_streamable_http_client

    monkeypatch.setitem(sys.modules, "mcp.client", client_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", streamable_http_mod)


def _make_wrapper(session: object, *, timeout: float = 0.1) -> MCPToolWrapper:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={"type": "object", "properties": {}},
    )
    return MCPToolWrapper(session, "test", tool_def, tool_timeout=timeout)


@pytest.mark.asyncio
async def test_connect_missing_servers_propagates_external_cancellation(monkeypatch) -> None:
    started = asyncio.Event()

    async def connect_mcp_servers(_servers: dict, _registry: ToolRegistry) -> dict:
        started.set()
        await asyncio.sleep(60)
        return {}

    class State:
        pass

    state = State()
    state._mcp_closing = False
    state._mcp_servers = {"test": MCPServerConfig(command="fake")}
    state._mcp_stacks = {}
    state._mcp_connecting = False
    monkeypatch.setattr(mcp_mod, "connect_mcp_servers", connect_mcp_servers)

    task = asyncio.create_task(mcp_mod.connect_missing_servers(state, ToolRegistry()))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert state._mcp_connecting is False


def test_wrapper_preserves_non_nullable_unions() -> None:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}],
                }
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    assert wrapper.parameters["properties"]["value"]["anyOf"] == [
        {"type": "string"},
        {"type": "integer"},
    ]


def test_wrapper_normalizes_nullable_property_type_union() -> None:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"]},
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    assert wrapper.parameters["properties"]["name"] == {"type": "string", "nullable": True}


def test_wrapper_normalizes_nullable_property_anyof() -> None:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "optional name",
                },
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    assert wrapper.parameters["properties"]["name"] == {
        "type": "string",
        "description": "optional name",
        "nullable": True,
    }


def test_wrapper_hoists_recursive_local_refs_into_defs() -> None:
    recursive_items_ref = "#/properties/filter/properties/items"
    tool_def = SimpleNamespace(
        name="search_dataset",
        description="search tool",
        inputSchema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"$ref": recursive_items_ref},
                        }
                    },
                    "required": ["items"],
                }
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    generated_ref = wrapper.parameters["properties"]["filter"]["properties"]["items"][
        "items"
    ]["$ref"]
    assert generated_ref.startswith("#/$defs/ref_")
    generated_name = generated_ref.removeprefix("#/$defs/")
    generated_schema = wrapper.parameters["$defs"][generated_name]
    assert generated_schema["type"] == "array"
    assert generated_schema["items"]["$ref"] == generated_ref


def test_wrapper_hoists_root_self_ref_into_defs() -> None:
    tool_def = SimpleNamespace(
        name="tree",
        description="tree tool",
        inputSchema={
            "type": "object",
            "properties": {
                "children": {"type": "array", "items": {"$ref": "#"}},
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    generated_ref = wrapper.parameters["properties"]["children"]["items"]["$ref"]
    assert generated_ref.startswith("#/$defs/ref_")
    generated_name = generated_ref.removeprefix("#/$defs/")
    assert wrapper.parameters["$defs"][generated_name]["properties"]["children"]["items"] == {
        "$ref": generated_ref
    }


def test_wrapper_preserves_existing_defs_refs() -> None:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={
            "type": "object",
            "$defs": {"value": {"type": "string"}},
            "properties": {"value": {"$ref": "#/$defs/value"}},
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    assert wrapper.parameters["properties"]["value"]["$ref"] == "#/$defs/value"
    assert wrapper.parameters["$defs"]["value"]["type"] == "string"


def test_wrapper_resolves_uri_encoded_json_pointer() -> None:
    tool_def = SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={
            "type": "object",
            "properties": {
                "space name/value": {"type": "string"},
                "alias": {"$ref": "#/properties/space%20name~1value"},
            },
        },
    )

    wrapper = MCPToolWrapper(SimpleNamespace(call_tool=None), "test", tool_def)

    generated_ref = wrapper.parameters["properties"]["alias"]["$ref"]
    assert generated_ref.startswith("#/$defs/ref_")
    generated_name = generated_ref.removeprefix("#/$defs/")
    assert wrapper.parameters["$defs"][generated_name] == {"type": "string"}


def test_normalize_windows_stdio_command_is_noop_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_mod.os, "name", "posix", raising=False)

    command, args, env = _normalize_windows_stdio_command(
        "npx",
        ["-y", "chrome-devtools-mcp@latest"],
        {"FOO": "bar"},
    )

    assert command == "npx"
    assert args == ["-y", "chrome-devtools-mcp@latest"]
    assert env == {"FOO": "bar"}


def test_normalize_windows_stdio_command_wraps_npx_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_mod.os, "name", "nt", raising=False)
    monkeypatch.setattr(
        mcp_mod.shutil,
        "which",
        lambda command, path=None: r"C:\Program Files\nodejs\npx.cmd",
    )
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    command, args, env = _normalize_windows_stdio_command(
        "npx",
        ["-y", "chrome-devtools-mcp@latest"],
        None,
    )

    assert command == r"C:\Windows\System32\cmd.exe"
    assert args == ["/d", "/c", "npx", "-y", "chrome-devtools-mcp@latest"]
    assert env is None


def test_normalize_windows_stdio_command_wraps_resolved_cmd_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_mod.os, "name", "nt", raising=False)

    def _fake_which(command: str, path: str | None = None) -> str:
        assert command == "custom-launcher"
        assert path == r"C:\Tools"
        return r"C:\Tools\custom-launcher.cmd"

    monkeypatch.setattr(mcp_mod.shutil, "which", _fake_which)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    command, args, _env = _normalize_windows_stdio_command(
        "custom-launcher",
        ["serve"],
        {"PATH": r"C:\Tools"},
    )

    assert command == r"C:\Windows\System32\cmd.exe"
    assert args == ["/d", "/c", "custom-launcher", "serve"]


def test_normalize_windows_stdio_command_keeps_real_executables_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_mod.os, "name", "nt", raising=False)

    command, args, env = _normalize_windows_stdio_command(
        "python.exe",
        ["-m", "http.server"],
        {"FOO": "bar"},
    )

    assert command == "python.exe"
    assert args == ["-m", "http.server"]
    assert env == {"FOO": "bar"}


def test_normalize_windows_stdio_command_skips_existing_shells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_mod.os, "name", "nt", raising=False)

    command, args, env = _normalize_windows_stdio_command(
        "cmd.exe",
        ["/c", "echo", "hello"],
        None,
    )

    assert command == "cmd.exe"
    assert args == ["/c", "echo", "hello"]
    assert env is None


@pytest.mark.asyncio
async def test_execute_returns_text_blocks() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        assert arguments == {"value": 1}
        return SimpleNamespace(content=[_FakeTextContent("hello"), 42])

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute(value=1)

    assert result == "hello\n42"


@pytest.mark.asyncio
async def test_execute_wraps_mcp_is_error_result() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        return SimpleNamespace(
            content=[_FakeTextContent("Error: server-side MCP failure")],
            isError=True,
        )

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute()

    assert result == "Error: server-side MCP failure"
    assert is_tool_error_result(result)


@pytest.mark.asyncio
async def test_execute_contains_malformed_success_result() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        return SimpleNamespace(content=None)

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute()

    assert result == "(MCP tool returned malformed content: TypeError)"
    assert is_tool_error_result(result)


@pytest.mark.asyncio
async def test_registry_adds_retry_hint_to_malformed_mcp_result() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        return SimpleNamespace(content=None)

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))
    registry = ToolRegistry()
    registry.register(wrapper)

    result = await registry.execute(wrapper.name, {})

    assert is_tool_error_result(result)
    assert "MCP tool returned malformed content" in result
    assert "Analyze the error above and try a different approach" in result


@pytest.mark.asyncio
async def test_execute_preserves_success_text_that_starts_with_error() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        return SimpleNamespace(
            content=[_FakeTextContent("Error: generated report successfully")],
            isError=False,
        )

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute()

    assert result == "Error: generated report successfully"
    assert not is_tool_error_result(result)


# Smallest valid 1x1 PNG, base64 without the data: prefix.
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_execute_persists_image_block_as_artifact(tmp_path: Path) -> None:
    from nanobot.config.loader import set_config_path

    set_config_path(tmp_path / "config.json")

    async def call_tool(_name: str, arguments: dict) -> object:
        return SimpleNamespace(
            content=[
                _FakeTextContent("here you go"),
                _FakeImageContent(_PNG_B64, "image/png"),
            ]
        )

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute(prompt="a cat", model="sdxl")

    payload = json.loads(result)
    assert payload["text"] == "here you go"
    assert len(payload["artifacts"]) == 1
    artifact = payload["artifacts"][0]
    assert artifact["mime"] == "image/png"
    assert artifact["prompt"] == "a cat"
    assert artifact["provider"] == "mcp:test"
    assert Path(artifact["path"]).is_file()
    # The base64 payload must NOT leak into the model-facing result.
    assert _PNG_B64 not in result
    assert "message tool" in payload["next_step"]


@pytest.mark.asyncio
async def test_execute_notes_unstorable_image_block(tmp_path: Path) -> None:
    from nanobot.config.loader import set_config_path

    set_config_path(tmp_path / "config.json")

    async def call_tool(_name: str, arguments: dict) -> object:
        return SimpleNamespace(content=[_FakeImageContent("not-valid-base64!!", "image/png")])

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute()

    assert result == "(MCP tool returned an image that could not be stored)"


@pytest.mark.asyncio
async def test_execute_returns_timeout_message() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        await asyncio.sleep(1)
        return SimpleNamespace(content=[])

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool), timeout=0.01)

    result = await wrapper.execute()

    assert result == "(MCP tool call timed out after 0.01s)"
    assert is_tool_error_result(result)


@pytest.mark.asyncio
async def test_execute_handles_server_cancelled_error() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        raise asyncio.CancelledError()

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute()

    assert result == "(MCP tool call was cancelled)"
    assert is_tool_error_result(result)


@pytest.mark.asyncio
async def test_cancel_scope_error_is_not_treated_as_external_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeTask:
        def cancelling(self) -> int:
            return 1

    monkeypatch.setattr(mcp_module.asyncio, "current_task", lambda: _FakeTask())

    assert (
        mcp_module._should_propagate_cancelled_error(
            asyncio.CancelledError("Cancelled via cancel scope test")
        )
        is False
    )
    assert mcp_module._should_propagate_cancelled_error(asyncio.CancelledError()) is True


def test_clear_current_task_cancellation_uncancels_until_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeTask:
        def __init__(self) -> None:
            self._count = 2

        def cancelling(self) -> int:
            return self._count

        def uncancel(self) -> None:
            self._count -= 1

    task = _FakeTask()
    monkeypatch.setattr(mcp_module.asyncio, "current_task", lambda: task)

    mcp_module._clear_current_task_cancellation()

    assert task.cancelling() == 0


@pytest.mark.asyncio
async def test_execute_re_raises_external_cancellation() -> None:
    started = asyncio.Event()

    async def call_tool(_name: str, arguments: dict) -> object:
        started.set()
        await asyncio.sleep(60)
        return SimpleNamespace(content=[])

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool), timeout=10)
    task = asyncio.create_task(wrapper.execute())
    await started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_execute_handles_generic_exception() -> None:
    async def call_tool(_name: str, arguments: dict) -> object:
        raise RuntimeError("boom")

    wrapper = _make_wrapper(SimpleNamespace(call_tool=call_tool))

    result = await wrapper.execute()

    assert result == "(MCP tool call failed: RuntimeError)"
    assert is_tool_error_result(result)


def _make_tool_def(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} tool",
        inputSchema={"type": "object", "properties": {}},
    )


def _make_fake_session(tool_names: list[str]) -> SimpleNamespace:
    async def initialize() -> None:
        return None

    async def list_tools() -> SimpleNamespace:
        return SimpleNamespace(tools=[_make_tool_def(name) for name in tool_names])

    return SimpleNamespace(initialize=initialize, list_tools=list_tools)


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_supports_raw_names(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo", "other"])
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", enabled_tools=["demo"])},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == ["mcp_test_demo"]


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_defaults_to_all(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo", "other"])
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake")},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == ["mcp_test_demo", "mcp_test_other"]


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_supports_wrapped_names(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo", "other"])
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", enabled_tools=["mcp_test_demo"])},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == ["mcp_test_demo"]


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_supports_limited_wrapped_names(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    long_tool_name = "tool-" + "very-long-name-" * 8
    wrapped_name = _sanitize_mcp_tool_name(f"mcp_test_{long_tool_name}")
    assert len(wrapped_name) == 64

    fake_mcp_runtime["session"] = _make_fake_session([long_tool_name, "other"])
    registry = ToolRegistry()
    stacks = await connect_mcp_servers(
        {"test": MCPServerConfig(command="fake", enabled_tools=[wrapped_name])},
        registry,
    )
    for stack in stacks.values():
        await stack.aclose()

    assert registry.tool_names == [wrapped_name]


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_empty_list_registers_none(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo", "other"])
    registry = ToolRegistry()
    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", enabled_tools=[])},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == []


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_empty_list_blocks_resources_and_prompts(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    """enabledTools: [] (deny-all) must also block resource and prompt registration."""
    fake_mcp_runtime["session"] = _make_fake_session_with_capabilities(
        tool_names=["demo"],
        resource_names=["secret_data"],
        prompt_names=["admin_prompt"],
    )
    registry = ToolRegistry()
    stacks = await connect_mcp_servers(
        {"test": MCPServerConfig(command="fake", enabled_tools=[])},
        registry,
    )
    for stack in stacks.values():
        await stack.aclose()

    assert registry.tool_names == []
    # Resources and prompts must also be blocked
    assert not any("secret_data" in name for name in registry.tool_names)
    assert not any("admin_prompt" in name for name in registry.tool_names)


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_specific_list_blocks_resources_and_prompts(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    """enabledTools with specific tool names must not leak resources or prompts."""
    fake_mcp_runtime["session"] = _make_fake_session_with_capabilities(
        tool_names=["demo", "other"],
        resource_names=["secret_data"],
        prompt_names=["admin_prompt"],
    )
    registry = ToolRegistry()
    stacks = await connect_mcp_servers(
        {"test": MCPServerConfig(command="fake", enabled_tools=["demo"])},
        registry,
    )
    for stack in stacks.values():
        await stack.aclose()

    # Only the allowed tool should be registered
    assert "mcp_test_demo" in registry.tool_names
    assert "mcp_test_other" not in registry.tool_names
    # Resources and prompts must not leak
    assert not any("secret_data" in name for name in registry.tool_names)
    assert not any("admin_prompt" in name for name in registry.tool_names)


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_wildcard_allows_resources_and_prompts(
    fake_mcp_runtime: dict[str, object | None],
) -> None:
    """enabledTools: ['*'] should allow all tools, resources, and prompts."""
    fake_mcp_runtime["session"] = _make_fake_session_with_capabilities(
        tool_names=["demo"],
        resource_names=["public_data"],
        prompt_names=["help_prompt"],
    )
    registry = ToolRegistry()
    stacks = await connect_mcp_servers(
        {"test": MCPServerConfig(command="fake", enabled_tools=["*"])},
        registry,
    )
    for stack in stacks.values():
        await stack.aclose()

    assert "mcp_test_demo" in registry.tool_names
    assert any("public_data" in name for name in registry.tool_names)
    assert any("help_prompt" in name for name in registry.tool_names)


@pytest.mark.asyncio
async def test_connect_mcp_servers_enabled_tools_warns_on_unknown_entries(
    fake_mcp_runtime: dict[str, object | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_mcp_runtime["session"] = _make_fake_session(["demo"])
    registry = ToolRegistry()
    warnings: list[str] = []

    def _warning(message: str, *args: object) -> None:
        warnings.append(message.format(*args))

    monkeypatch.setattr("nanobot.agent.tools.mcp.logger.warning", _warning)

    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        await connect_mcp_servers(
            {"test": MCPServerConfig(command="fake", enabled_tools=["unknown"])},
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert registry.tool_names == []
    assert warnings
    assert "enabledTools entries not found: unknown" in warnings[-1]
    assert "Available raw names: demo" in warnings[-1]
    assert "Available wrapped names: mcp_test_demo" in warnings[-1]


@pytest.mark.asyncio
async def test_connect_mcp_servers_continues_after_server_side_cancellation(
    fake_mcp_runtime: dict[str, object | None], monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"initialize_calls": 0}

    async def initialize() -> None:
        state["initialize_calls"] += 1
        if state["initialize_calls"] == 1:
            raise asyncio.CancelledError("Cancelled via cancel scope test")

    async def list_tools() -> SimpleNamespace:
        return SimpleNamespace(tools=[_make_tool_def("demo")])

    fake_mcp_runtime["session"] = SimpleNamespace(
        initialize=initialize,
        list_tools=list_tools,
    )
    registry = ToolRegistry()
    warnings: list[str] = []

    def _warning(message: str, *args: object) -> None:
        warnings.append(message.format(*args))

    monkeypatch.setattr("nanobot.agent.tools.mcp.logger.warning", _warning)

    stack = AsyncExitStack()
    await stack.__aenter__()
    try:
        connected_count, cancelled_count = await connect_mcp_servers(
            {
                "cancelled": MCPServerConfig(command="fake"),
                "healthy": MCPServerConfig(command="fake"),
            },
            registry,
            stack,
        )
    finally:
        await stack.aclose()

    assert connected_count == 1
    assert cancelled_count == 1
    assert registry.tool_names == ["mcp_healthy_demo"]
    assert warnings
    assert "cancelled by server/SDK" in warnings[-1]
