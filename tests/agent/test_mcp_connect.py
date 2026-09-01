"""MCP connect-retry semantics for AgentLoop (re-applied on upstream architecture).

The dev-clean-era loop-local helpers (`_mcp_connected`,
`_should_propagate_cancelled_error`, `_clear_current_task_cancellation`)
were superseded upstream: retry state now lives in
`nanobot.agent.tools.mcp.connect_missing_servers` (per-state `_mcp_stacks`)
and cancellation discrimination uses `nanobot.utils.cancellation.
task_is_cancelling`.  These tests pin that current behavior.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import MCPServerConfig


def _make_loop(tmp_path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        mcp_servers={"demo": MCPServerConfig(command="fake")},
    )


@pytest.fixture
def mcp_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import nanobot.agent.tools.mcp as mcp_mod

    warnings: list[str] = []

    def _warning(message: str, *args: object) -> None:
        warnings.append(str(message).format(*args))

    monkeypatch.setattr(mcp_mod.logger, "warning", _warning)
    return warnings


@pytest.mark.asyncio
async def test_connect_mcp_retries_when_nothing_connected(
    tmp_path, monkeypatch: pytest.MonkeyPatch, mcp_warnings: list[str]
) -> None:
    loop = _make_loop(tmp_path)
    attempts: list[tuple[str, ...]] = []

    async def fake_connect_missing(missing_servers, _registry):
        attempts.append(tuple(missing_servers))
        return {}

    monkeypatch.setattr("nanobot.agent.tools.mcp.connect_mcp_servers", fake_connect_missing)

    await loop._connect_mcp()

    assert loop._mcp_stacks == {}
    assert loop._mcp_connecting is False
    assert mcp_warnings
    assert "will retry next message" in mcp_warnings[-1]

    # A later message re-attempts the still-missing server.
    await loop._connect_mcp()
    assert attempts == [("demo",), ("demo",)]


@pytest.mark.asyncio
async def test_cancelled_mcp_connect_without_external_cancel_is_retried(
    tmp_path, monkeypatch: pytest.MonkeyPatch, mcp_warnings: list[str]
) -> None:
    loop = _make_loop(tmp_path)

    async def fake_connect_missing(_missing_servers, _registry):
        raise asyncio.CancelledError()

    monkeypatch.setattr("nanobot.agent.tools.mcp.connect_mcp_servers", fake_connect_missing)

    await loop._connect_mcp()

    assert loop._mcp_stacks == {}
    assert loop._mcp_connecting is False
    assert mcp_warnings
    assert "will retry next message" in mcp_warnings[-1]


@pytest.mark.asyncio
async def test_external_task_cancellation_propagates_out_of_mcp_connect(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _make_loop(tmp_path)

    async def fake_connect_missing(_missing_servers, _registry):
        raise asyncio.CancelledError()

    monkeypatch.setattr("nanobot.agent.tools.mcp.connect_mcp_servers", fake_connect_missing)
    monkeypatch.setattr("nanobot.agent.tools.mcp.task_is_cancelling", lambda: True)

    with pytest.raises(asyncio.CancelledError):
        await loop._connect_mcp()

    assert loop._mcp_stacks == {}
    assert loop._mcp_connecting is False


@pytest.mark.asyncio
async def test_connect_mcp_records_successful_connections_on_state(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _make_loop(tmp_path)
    connection = MagicMock()

    async def fake_connect_missing(_missing_servers, _registry):
        return {"demo": connection}

    async def no_reconnect_handlers(state, registry, connected):
        return None

    monkeypatch.setattr("nanobot.agent.tools.mcp.connect_mcp_servers", fake_connect_missing)
    monkeypatch.setattr(
        "nanobot.agent.tools.mcp._attach_reconnect_handlers", no_reconnect_handlers
    )

    await loop._connect_mcp()

    assert loop._mcp_stacks == {"demo": connection}
    assert loop._mcp_connecting is False
    # Nothing missing now: subsequent messages do not re-connect.
    await loop._connect_mcp()
    assert loop._mcp_stacks == {"demo": connection}
