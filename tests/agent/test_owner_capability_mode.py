from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.cron.service import CronService
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_loop(
    tmp_path: Path,
    owner_ids: list[str] | None = None,
    *,
    with_cron: bool = False,
) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    cron_service = CronService(tmp_path / "cron" / "jobs.json") if with_cron else None
    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        owner_ids=owner_ids or [],
        cron_service=cron_service,
    )


def _tool_names_from_last_call(loop: AgentLoop) -> set[str]:
    tools = loop.provider.chat_with_retry.call_args.kwargs["tools"]
    return {tool["function"]["name"] for tool in tools}


def test_chat_only_prompt_excludes_private_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "SOUL.md").write_text("Friendly public persona", encoding="utf-8")
    (workspace / "USER.md").write_text("Owner private profile", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("Repository-only instructions", encoding="utf-8")
    (workspace / "memory").mkdir()
    (workspace / "memory" / "MEMORY.md").write_text("Private long-term memory", encoding="utf-8")
    skill_dir = workspace / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo skill", encoding="utf-8")

    builder = ContextBuilder(workspace)
    prompt = builder.build_system_prompt(capability_mode="chat_only")

    assert "Friendly public persona" in prompt
    assert "Owner private profile" not in prompt
    assert "Private long-term memory" not in prompt
    assert "Repository-only instructions" not in prompt
    assert "<skills>" not in prompt
    assert "chat-only mode" in prompt
    assert "web_search" in prompt
    assert "web_fetch" in prompt


@pytest.mark.asyncio
async def test_chat_only_allows_web_search(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"])
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="hello", tool_calls=[]))

    response = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="guest", chat_id="room1", content="hi"),
    )

    assert response is not None
    assert response.content == "hello"
    assert "web_search" in _tool_names_from_last_call(loop)


@pytest.mark.asyncio
async def test_chat_only_allows_web_fetch(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"])
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="hello", tool_calls=[]))

    response = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="guest", chat_id="room1", content="hi"),
    )

    assert response is not None
    assert "web_fetch" in _tool_names_from_last_call(loop)


@pytest.mark.asyncio
async def test_chat_only_blocks_write_file(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"], with_cron=True)
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="hello", tool_calls=[]))

    response = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="guest", chat_id="room1", content="hi"),
    )

    assert response is not None
    tool_names = _tool_names_from_last_call(loop)
    assert "write_file" not in tool_names
    assert "exec" not in tool_names
    assert "cron" not in tool_names


@pytest.mark.asyncio
async def test_full_mode_allows_all_tools(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"])
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="hello", tool_calls=[]))

    response = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="owner", chat_id="room1", content="hi"),
    )

    assert response is not None
    tool_names = _tool_names_from_last_call(loop)
    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    assert "write_file" in tool_names
    assert "exec" in tool_names


@pytest.mark.asyncio
async def test_local_cli_keeps_tools_without_owner_binding(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="hello", tool_calls=[]))

    response = await loop._process_message(
        InboundMessage(channel="cli", sender_id="user", chat_id="direct", content="hi"),
    )

    assert response is not None
    tools = loop.provider.chat_with_retry.call_args.kwargs["tools"]
    assert any(tool["function"]["name"] == "read_file" for tool in tools)


@pytest.mark.asyncio
async def test_non_owner_tool_call_is_blocked_even_if_model_attempts_it(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"])
    protected_file = tmp_path / "USER.md"
    protected_file.write_text("Owner profile", encoding="utf-8")
    calls = iter([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call1",
                    name="write_file",
                    arguments={"path": str(protected_file), "content": "Overwritten"},
                )
            ],
        ),
        LLMResponse(content="Denied", tool_calls=[]),
    ])
    loop.provider.chat_with_retry = AsyncMock(side_effect=lambda *args, **kwargs: next(calls))
    loop.tools.execute = AsyncMock(return_value="should not run")

    response = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="guest", chat_id="room1", content="change owner"),
    )

    assert response is not None
    assert response.content == "Denied"
    assert protected_file.read_text(encoding="utf-8") == "Owner profile"
    loop.tools.execute.assert_not_called()


@pytest.mark.asyncio
async def test_non_owner_remote_status_is_denied(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"])

    response = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="guest", chat_id="room1", content="/status"),
    )

    assert response is not None
    assert "only available" in response.content.lower()
    assert response.metadata == {"render_as": "text"}

@pytest.mark.asyncio
async def test_internal_automation_allows_task_tools_but_blocks_delegation(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"], with_cron=True)
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))

    response = await loop._process_message(
        InboundMessage(
            channel="telegram",
            sender_id="cron",
            chat_id="room1",
            content="run scheduled task",
            metadata={"_internal_automation": "cron"},
        ),
        session_key="cron:job1",
    )

    assert response is not None
    tool_names = _tool_names_from_last_call(loop)
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert "edit_file" in tool_names
    assert "list_dir" in tool_names
    assert "exec" in tool_names
    assert "message" in tool_names
    assert "cron" in tool_names
    assert "spawn" not in tool_names
    assert "codex_delegate" not in tool_names
    assert "codex_status" not in tool_names
    assert "codex_resume" not in tool_names


@pytest.mark.asyncio
async def test_internal_automation_still_cannot_run_owner_only_commands(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"])

    response = await loop._process_message(
        InboundMessage(
            channel="telegram",
            sender_id="heartbeat",
            chat_id="room1",
            content="/status",
            metadata={"_internal_automation": "heartbeat"},
        ),
        session_key="heartbeat",
    )

    assert response is not None
    assert "only available" in response.content.lower()


@pytest.mark.asyncio
async def test_sender_named_cron_without_internal_tag_stays_chat_only(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, owner_ids=["telegram:owner"])
    loop.provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))

    response = await loop._process_message(
        InboundMessage(channel="telegram", sender_id="cron", chat_id="room1", content="hi"),
    )

    assert response is not None
    tool_names = _tool_names_from_last_call(loop)
    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    assert "write_file" not in tool_names
    assert "exec" not in tool_names
