"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.codex_jobs import CodexJobManager
from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryConsolidator
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.codex import CodexDelegateTool, CodexResumeTool, CodexStatusTool
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig, WebSearchConfig
    from nanobot.cron.service import CronService


def _should_propagate_cancelled_error(exc: asyncio.CancelledError) -> bool:
    """Return True when a cancellation should escape this MCP compatibility layer."""
    if "Cancelled via cancel scope" in str(exc):
        return False
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


def _clear_current_task_cancellation() -> None:
    """Clear swallowed cancellation state so later awaits can proceed."""
    task = asyncio.current_task()
    uncancel = getattr(task, "uncancel", None)
    if task is None or not callable(uncancel):
        return
    while task.cancelling() > 0:
        uncancel()


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 16_000
    _CHAT_ONLY_ALLOWED_TOOLS = frozenset({"web_search", "web_fetch"})
    _AUTOMATION_BLOCKED_TOOLS = frozenset({
        "spawn",
        "codex_delegate",
        "codex_status",
        "codex_resume",
        "cron",
    })
    _AUTOMATION_KINDS = frozenset({"cron", "heartbeat"})

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        context_window_tokens: int = 65_536,
        web_search_config: WebSearchConfig | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        owner_ids: list[str] | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig, WebSearchConfig

        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.context_window_tokens = context_window_tokens
        self.web_search_config = web_search_config or WebSearchConfig()
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace
        self.owner_ids = {str(item).strip() for item in (owner_ids or []) if str(item).strip()}

        self.context = ContextBuilder(workspace, owner_ids=list(self.owner_ids))
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.codex_jobs = CodexJobManager(workspace=workspace, bus=bus)
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_search_config=self.web_search_config,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._processing_lock = asyncio.Lock()
        generation = getattr(provider, "generation", None)
        max_completion_tokens = getattr(generation, "max_tokens", 4096)
        if not isinstance(max_completion_tokens, int) or max_completion_tokens <= 0:
            max_completion_tokens = 4096
        self.memory_consolidator = MemoryConsolidator(
            workspace=workspace,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            max_completion_tokens=max_completion_tokens,
        )
        self._register_default_tools()

    def _resolve_conversation_type(self, msg: InboundMessage) -> str:
        """Infer whether the current message is a direct, group, or thread conversation."""
        metadata = msg.metadata or {}

        if (
            metadata.get("thread_id")
            or metadata.get("message_thread_id")
            or metadata.get("thread_root_event_id")
        ):
            return "thread"

        chat_type = str(metadata.get("chat_type") or "").strip().lower()
        if chat_type in {"private", "direct", "p2p", "single", "dm", "im"}:
            return "direct"
        if chat_type in {"group", "supergroup", "channel"}:
            return "group"
        if chat_type in {"thread", "forum"}:
            return "thread"

        channel_type = str(metadata.get("channel_type") or "").strip().lower()
        if channel_type == "im":
            return "direct"
        if channel_type:
            return "group"

        conversation_type = str(metadata.get("conversation_type") or "").strip().lower()
        if conversation_type in {"1", "single", "private", "direct"}:
            return "direct"
        if conversation_type in {"2", "group"}:
            return "group"

        if metadata.get("is_group") is True:
            return "group"

        chat_id = str(msg.chat_id)
        sender_id = str(msg.sender_id)
        if chat_id == sender_id:
            return "direct"
        if chat_id.startswith("group:"):
            return "group"
        return "shared"

    def _is_owner(self, msg: InboundMessage) -> bool | None:
        """Return whether the sender matches configured owner IDs."""
        if not self.owner_ids:
            return None

        sender_id = str(msg.sender_id).strip()
        candidates = {sender_id, f"{msg.channel}:{sender_id}"}
        return any(candidate in self.owner_ids for candidate in candidates)

    @staticmethod
    def _is_trusted_local_message(msg: InboundMessage) -> bool:
        """Return True for trusted local/internal control-plane messages."""
        return msg.channel in {"cli", "system"}

    def _has_full_capabilities(self, msg: InboundMessage) -> bool:
        """Return whether the message may use tools, skills, and admin commands."""
        if self._is_trusted_local_message(msg):
            return True
        return self._is_owner(msg) is True

    def _capability_mode(self, msg: InboundMessage) -> str:
        """Return the capability mode for the current message."""
        if self._has_full_capabilities(msg):
            return "full"
        if self._is_internal_automation_message(msg):
            return "automation"
        return "chat_only"

    def _automation_kind(self, msg: InboundMessage) -> str | None:
        """Return trusted internal automation kind when present."""
        metadata = msg.metadata or {}
        raw = str(metadata.get("_internal_automation") or "").strip().lower()
        if raw in self._AUTOMATION_KINDS:
            return raw
        return None

    def _is_internal_automation_message(self, msg: InboundMessage) -> bool:
        """Return True for internally-tagged cron/heartbeat executions."""
        return self._automation_kind(msg) is not None

    def _allowed_tool_names_for_message(self, msg: InboundMessage) -> set[str]:
        """Return the allowlisted tool names for this message."""
        registered_tools = set(self.tools.tool_names)
        if self._has_full_capabilities(msg):
            return registered_tools
        if self._is_internal_automation_message(msg):
            return registered_tools.difference(self._AUTOMATION_BLOCKED_TOOLS)

        # chat_only mode remains deny-by-default and only permits explicitly
        # allowlisted, read-only web tools.
        return registered_tools.intersection(self._CHAT_ONLY_ALLOWED_TOOLS)

    @staticmethod
    def _owner_only_message() -> str:
        """Return a consistent denial message for restricted actions."""
        return "This action is only available to the configured owner or from the local CLI."

    def _is_owner_only_command(self, command: str) -> bool:
        """Return whether a slash command requires full capabilities."""
        return command in {"/restart", "/status", "/stop"}

    def _speaker_context_kwargs(self, msg: InboundMessage) -> dict[str, Any]:
        """Build speaker metadata for prompt runtime context."""
        metadata = msg.metadata or {}
        return {
            "sender_id": str(msg.sender_id),
            "sender_name": str(metadata.get("sender_name") or "").strip() or None,
            "sender_username": str(metadata.get("sender_username") or "").strip() or None,
            "conversation_type": self._resolve_conversation_type(msg),
            "is_owner": self._is_owner(msg),
        }

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(config=self.web_search_config, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        self.tools.register(CodexDelegateTool(manager=self.codex_jobs))
        self.tools.register(CodexStatusTool(manager=self.codex_jobs))
        self.tools.register(CodexResumeTool(manager=self.codex_jobs))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            connected_count, cancelled_count = await connect_mcp_servers(
                self._mcp_servers, self.tools, self._mcp_stack
            )
            if connected_count > 0 or cancelled_count == 0:
                self._mcp_connected = True
            else:
                logger.warning(
                    "MCP connection setup was cancelled by server/SDK (will retry next message)"
                )
                await self._cleanup_mcp_stack()
        except asyncio.CancelledError as e:
            if _should_propagate_cancelled_error(e):
                await self._cleanup_mcp_stack()
                raise
            _clear_current_task_cancellation()
            logger.warning(
                "MCP connection setup was cancelled by server/SDK (will retry next message): {}",
                e,
            )
            await self._cleanup_mcp_stack()
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            await self._cleanup_mcp_stack()
        finally:
            self._mcp_connecting = False

    async def _cleanup_mcp_stack(self) -> None:
        """Close and clear the active MCP stack after a failed connect attempt."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass
            self._mcp_stack = None

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron", "codex_delegate", "codex_status", "codex_resume"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>...</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}...")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        allowed_tool_names = set(self.tools.tool_names if allowed_tool_names is None else allowed_tool_names)

        while iteration < self.max_iterations:
            iteration += 1

            tool_defs = self.tools.get_definitions(allowed_names=allowed_tool_names)

            response = await self.provider.chat_with_retry(
                messages=messages,
                tools=tool_defs,
                model=self.model,
            )

            if response.has_tool_calls:
                if on_progress:
                    thought = self._strip_think(response.content)
                    if thought:
                        await on_progress(thought)
                    if allowed_tool_names:
                        tool_hint = self._tool_hint(response.tool_calls)
                        tool_hint = self._strip_think(tool_hint)
                        await on_progress(tool_hint, tool_hint=True)

                tool_call_dicts = [
                    tc.to_openai_tool_call()
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    if tool_call.name not in allowed_tool_names:
                        logger.warning(
                            "Blocked unauthorized tool call in restricted session: {}",
                            tool_call.name,
                        )
                        result = (
                            "Error: tool use is disabled in this conversation. "
                            "Only the configured owner or local CLI can use tools."
                        )
                    else:
                        tools_used.append(tool_call.name)
                        args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                        logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                        result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history - they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self.codex_jobs.restore_pending_jobs()
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            cmd = msg.content.strip().lower()
            if cmd == "/stop":
                if self._has_full_capabilities(msg):
                    await self._handle_stop(msg)
                else:
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=self._owner_only_message(),
                    ))
            elif cmd == "/restart":
                if self._has_full_capabilities(msg):
                    await self._handle_restart(msg)
                else:
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=self._owner_only_message(),
                    ))
            else:
                task = asyncio.create_task(self._dispatch(msg))
                self._active_tasks.setdefault(msg.session_key, []).append(task)
                task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _handle_restart(self, msg: InboundMessage) -> None:
        """Restart the process in-place via os.execv."""
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        ))

        async def _do_restart():
            await asyncio.sleep(1)
            # Use -m nanobot instead of sys.argv[0] for Windows compatibility
            # (sys.argv[0] may be just "nanobot" without full path on Windows)
            os.execv(sys.executable, [sys.executable, "-m", "nanobot"] + sys.argv[1:])

        asyncio.create_task(_do_restart())

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        await self.codex_jobs.close()
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=0)
            # Subagent results should be assistant role, other system messages use user role
            current_role = "assistant" if msg.sender_id in {"subagent", "codex_job"} else "user"
            fitted_history, estimated, source = self.memory_consolidator.fit_history_within_budget(
                history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                current_role=current_role,
            )
            if len(fitted_history) != len(history):
                logger.warning(
                    "Trimmed system-message history for {} from {} to {} messages to fit context budget ({} via {})",
                    key,
                    len(history),
                    len(fitted_history),
                    estimated,
                    source,
                )
            messages = self.context.build_messages(
                history=fitted_history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                sender_id=msg.sender_id,
                current_role=current_role,
                capability_mode="full",
            )
            final_content, _, all_msgs = await self._run_agent_loop(
                messages,
                allowed_tool_names=set(self.tools.tool_names),
            )
            self._save_turn(session, all_msgs, 1 + len(fitted_history))
            self.sessions.save(session)
            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)

        # Slash commands
        cmd = msg.content.strip().lower()
        if self._is_owner_only_command(cmd) and not self._has_full_capabilities(msg):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=self._owner_only_message(),
                metadata={"render_as": "text"},
            )
        if cmd == "/new":
            snapshot = session.messages[session.last_consolidated:]
            session.clear()
            self.sessions.save(session)
            self.sessions.invalidate(session.key)

            if snapshot:
                self._schedule_background(self.memory_consolidator.archive_messages(snapshot))

            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/help":
            lines = [
                "nanobot commands:",
                "/new - Start a new conversation",
                "/stop - Stop the current task",
                "/restart - Restart the bot",
                "/help - Show available commands",
            ]
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines),
            )
        await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=0)
        fitted_history, estimated, source = self.memory_consolidator.fit_history_within_budget(
            history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )
        if len(fitted_history) != len(history):
            logger.warning(
                "Trimmed history for {} from {} to {} messages to fit context budget ({} via {})",
                key,
                len(history),
                len(fitted_history),
                estimated,
                source,
            )
        capability_mode = self._capability_mode(msg)
        allowed_tool_names = self._allowed_tool_names_for_message(msg)
        initial_messages = self.context.build_messages(
            history=fitted_history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            **self._speaker_context_kwargs(msg),
            capability_mode=capability_mode,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages,
            on_progress=on_progress or _bus_progress,
            allowed_tool_names=allowed_tool_names,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._save_turn(session, all_msgs, 1 + len(fitted_history))
        self.sessions.save(session)
        self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages - they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    runtime_meta, user_text = ContextBuilder.extract_runtime_metadata(content)
                    prefix = ContextBuilder.build_historical_speaker_prefix(runtime_meta)
                    if user_text.strip():
                        entry["content"] = f"{prefix or ''}{user_text}".strip()
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    runtime_meta: dict[str, str] = {}
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            runtime_meta, _ = ContextBuilder.extract_runtime_metadata(c["text"])
                            continue  # Strip runtime context from multimodal messages
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            path = (c.get("_meta") or {}).get("path", "")
                            placeholder = f"[image: {path}]" if path else "[image]"
                            filtered.append({"type": "text", "text": placeholder})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    prefix = ContextBuilder.build_historical_speaker_prefix(runtime_meta)
                    if prefix:
                        if filtered[0].get("type") == "text" and isinstance(filtered[0].get("text"), str):
                            filtered[0]["text"] = prefix + filtered[0]["text"]
                        else:
                            filtered.insert(0, {"type": "text", "text": prefix.rstrip()})
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        sender_id: str = "user",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self.codex_jobs.restore_pending_jobs()
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            metadata=metadata or {},
        )
        response = await self._process_message(msg, session_key=session_key, on_progress=on_progress)
        return response.content if response else ""
