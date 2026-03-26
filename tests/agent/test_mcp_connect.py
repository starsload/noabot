from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from nanobot.agent import loop as loop_module
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


@pytest.mark.asyncio
async def test_connect_mcp_retries_when_all_servers_are_cancelled(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _make_loop(tmp_path)
    warnings: list[str] = []

    async def fake_connect_mcp_servers(_mcp_servers, _registry, _stack) -> tuple[int, int]:
        return 0, 1

    def _warning(message: str, *args: object) -> None:
        warnings.append(message.format(*args))

    monkeypatch.setattr("nanobot.agent.tools.mcp.connect_mcp_servers", fake_connect_mcp_servers)
    monkeypatch.setattr("nanobot.agent.loop.logger.warning", _warning)

    await loop._connect_mcp()

    assert loop._mcp_connected is False
    assert loop._mcp_stack is None
    assert loop._mcp_connecting is False
    assert warnings
    assert "will retry next message" in warnings[-1]


@pytest.mark.asyncio
async def test_loop_cancel_scope_error_is_not_treated_as_external_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeTask:
        def cancelling(self) -> int:
            return 1

    monkeypatch.setattr(loop_module.asyncio, "current_task", lambda: _FakeTask())

    assert (
        loop_module._should_propagate_cancelled_error(
            asyncio.CancelledError("Cancelled via cancel scope test")
        )
        is False
    )
    assert loop_module._should_propagate_cancelled_error(asyncio.CancelledError()) is True


def test_loop_clear_current_task_cancellation_uncancels_until_clear(
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
    monkeypatch.setattr(loop_module.asyncio, "current_task", lambda: task)

    loop_module._clear_current_task_cancellation()

    assert task.cancelling() == 0
