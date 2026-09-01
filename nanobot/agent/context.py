"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools import image_generation as image_generation_tools
from nanobot.agent.tools import mcp as mcp_tools
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.apps.cli import utils as cli_app_utils
from nanobot.bus.events import InboundMessage
from nanobot.runtime_context import (
    RUNTIME_CONTEXT_END,
    RUNTIME_CONTEXT_MESSAGE_META,
    RUNTIME_CONTEXT_TAG,
    RuntimeContextBlock,
    append_runtime_context,
)
from nanobot.utils.helpers import (
    current_time_str,
    detect_image_mime,
    load_bundled_template,
    truncate_text_to_tokens,
)
from nanobot.utils.prompt_templates import render_template


def session_extra(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return persisted kwargs for turn-attached capabilities."""
    return cli_app_utils.session_extra(metadata) | mcp_tools.session_extra(metadata)


async def connect_mcp(state: Any, tools: ToolRegistry) -> None:
    await mcp_tools.connect_missing_servers(state, tools)


async def close_mcp(state: Any) -> None:
    await mcp_tools.close_mcp_servers(state)


async def handle_runtime_control(state: Any, msg: InboundMessage, tools: ToolRegistry) -> bool:
    for handler in (
        image_generation_tools.handle_runtime_control,
        mcp_tools.handle_runtime_control,
    ):
        if await handler(state, msg, tools):
            return True
    return False


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]
    _SKIPPABLE_DEFAULTS = {"AGENTS.md", "USER.md"}
    # chat_only turns never see repo instructions or the owner's private
    # profile; only the public persona file is loaded.
    _CHAT_ONLY_BOOTSTRAP_FILES = ("SOUL.md",)
    _RUNTIME_CONTEXT_TAG = RUNTIME_CONTEXT_TAG
    _MAX_RECENT_HISTORY = 50
    _MAX_HISTORY_TOKENS = 8_000  # hard cap on recent history section size (tokens)
    _RUNTIME_CONTEXT_END = RUNTIME_CONTEXT_END

    def __init__(self, workspace: Path, timezone: str | None = None, disabled_skills: list[str] | None = None):
        self.workspace = workspace
        self.timezone = timezone
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace, disabled_skills=set(disabled_skills) if disabled_skills else None)

    def build_system_prompt(
        self,
        *,
        active_skill_names: Sequence[str] | None = None,
        channel: str | None = None,
        session_summary: str | None = None,
        workspace: Path | None = None,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        unified_session: bool = False,
        capability_mode: str = "full",
        skill_names: Sequence[str] | None = None,
        allowed_tool_names: Sequence[str] | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        root = workspace or self.workspace
        parts = [self._get_identity(channel=channel, workspace=root)]

        capability_policy = self._build_capability_policy(
            capability_mode,
            allowed_tool_names,
        )
        if capability_policy:
            parts.append(capability_policy)

        bootstrap = self._load_bootstrap_files(root, capability_mode=capability_mode)
        if bootstrap:
            parts.append(bootstrap)

        parts.append(render_template("agent/tool_contract.md"))

        if capability_mode != "chat_only":
            memory = self.memory.read_memory()
            if memory and not self._is_template_content(memory, "memory/MEMORY.md"):
                parts.append(f"# Memory\n\n## Long-term Memory\n{memory}")

        if capability_mode == "chat_only":
            # Only explicitly surfaced safe skills are rendered; always-skills,
            # slash-invoked skills, and the skills summary stay hidden.
            active_skills = list(skill_names or ())
            if active_skills:
                active_content = self.skills.load_skills_for_context(active_skills)
                if active_content:
                    parts.append(f"# Active Skills\n\n{active_content}")
        else:
            active_skills = self.skills.get_always_skills()
            active_skills.extend(
                name
                for name in (active_skill_names or ())
                if name not in active_skills
            )
            active_skills.extend(
                name
                for name in (skill_names or ())
                if name not in active_skills
            )
            if active_skills:
                active_content = self.skills.load_skills_for_context(active_skills)
                if active_content:
                    parts.append(f"# Active Skills\n\n{active_content}")

            skills_summary = self.skills.build_skills_summary(exclude=set(active_skills))
            if skills_summary:
                parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

        if include_memory_recent_history and capability_mode != "chat_only":
            entries = self.memory.read_recent_history_for_prompt(
                since_cursor=self.memory.get_last_dream_cursor(),
                session_key=session_key,
                unified_session=unified_session,
            )
            if entries:
                capped = entries[-self._MAX_RECENT_HISTORY:]
                history_text = "\n".join(
                    f"- [{e['timestamp']}] {e['content']}" for e in capped
                )
                history_text = truncate_text_to_tokens(history_text, self._MAX_HISTORY_TOKENS)
                parts.append("# Recent History\n\n" + history_text)

        if session_summary:
            parts.append(f"[Archived Context Summary]\n\n{session_summary}")

        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _build_capability_policy(
        capability_mode: str,
        allowed_tool_names: Sequence[str] | None,
    ) -> str:
        """Render the capability-mode guidance block appended to the identity section."""
        if capability_mode == "chat_only":
            tools_text = ", ".join(sorted(allowed_tool_names)) if allowed_tool_names else ""
            tools_line = (
                f"- Only low-risk tools are available in this conversation: {tools_text}."
                if tools_text
                else "- Only low-risk tools are available in this conversation."
            )
            return (
                "## Capability Mode\n"
                "- This conversation is running in chat-only mode.\n"
                f"{tools_line}\n"
                "- Filesystem access, shell commands, cron, MCP, subagents, background jobs, "
                "and other non-surfaced skills are unavailable.\n"
                "- If asked to modify files, run commands, schedule tasks, or use integrations, "
                "explain that only the configured owner or local CLI can do that.\n"
                "- Do not reveal private owner profile details or long-term memory "
                "to a non-owner speaker."
            )
        if capability_mode == "automation":
            return (
                "## Capability Mode\n"
                "- This turn runs as internal automation (cron or heartbeat).\n"
                "- Task tools are available, but delegation, subagents, and desktop "
                "control are not.\n"
                "- Do not reveal private owner profile details or long-term memory "
                "to non-owner speakers in delivered messages."
            )
        return ""

    def _get_identity(self, channel: str | None = None, workspace: Path | None = None) -> str:
        """Get the core identity section."""
        root = workspace or self.workspace
        workspace_path = str(root.expanduser().resolve())
        agent_workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            agent_workspace_path=agent_workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    @staticmethod
    def _build_runtime_context(
        channel: str | None,
        chat_id: str | None,
        timezone: str | None = None,
        *,
        sender_id: str | None = None,
        sender_name: str | None = None,
        sender_username: str | None = None,
        conversation_type: str | None = None,
        is_owner: bool | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection with the user message."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if conversation_type:
            lines.append(f"Conversation Type: {conversation_type}")
        if sender_id:
            lines.append(f"Speaker ID: {sender_id}")
        if sender_name:
            lines.append(f"Speaker Name: {sender_name}")
        if sender_username:
            lines.append(f"Speaker Username: {sender_username}")
        if is_owner is not None:
            lines.append(f"Is Owner: {'true' if is_owner else 'false'}")
        if sender_id or sender_name or sender_username:
            lines.append("Current speaker may not be the workspace owner from USER.md.")
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    @staticmethod
    def extract_runtime_metadata(content: str) -> tuple[dict[str, str], str]:
        """Split a merged runtime-context user message into metadata and user text."""
        if not isinstance(content, str) or not content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
            return {}, content if isinstance(content, str) else ""

        header, body = (content.split("\n\n", 1) + [""])[:2]
        metadata: dict[str, str] = {}
        for line in header.splitlines()[1:]:
            if ": " not in line:
                continue
            key, value = line.split(": ", 1)
            metadata[key] = value
        return metadata, body

    @staticmethod
    def build_historical_speaker_prefix(metadata: dict[str, str]) -> str | None:
        """Return a short prefix that preserves speaker identity in shared histories."""
        speaker_id = metadata.get("Speaker ID", "").strip()
        if not speaker_id:
            return None

        conversation_type = metadata.get("Conversation Type", "").strip().lower()
        chat_id = metadata.get("Chat ID", "").strip()
        should_prefix = conversation_type in {"group", "thread", "shared"} or (
            chat_id and speaker_id != chat_id
        )
        if not should_prefix:
            return None

        speaker_name = (
            metadata.get("Speaker Name", "").strip()
            or metadata.get("Speaker Username", "").strip()
            or speaker_id
        )
        label = speaker_name if speaker_name == speaker_id else f"{speaker_name} ({speaker_id})"
        owner = metadata.get("Is Owner", "").strip().lower()
        owner_suffix = f", owner={owner}" if owner in {"true", "false"} else ""
        return f"[speaker: {label}{owner_suffix}] "

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        if isinstance(left, str) and isinstance(right, str):
            if not left:
                return right
            if not right:
                return left
            return f"{left}\n\n{right}"

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [
                    cast(dict[str, Any], item)
                    if isinstance(item, dict)
                    else {"type": "text", "text": str(item)}
                    for item in cast(list[Any], value)
                ]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _load_bootstrap_files(
        self,
        workspace: Path | None = None,
        capability_mode: str = "full",
    ) -> str:
        """Load project instructions plus the agent's global profile files."""
        parts: list[str] = []
        project_root = workspace or self.workspace
        sources = [
            ("AGENTS.md", project_root),
            ("SOUL.md", self.workspace),
            ("USER.md", self.workspace),
        ]
        if capability_mode == "chat_only":
            sources = [(name, root) for name, root in sources if name in self._CHAT_ONLY_BOOTSTRAP_FILES]

        for filename, root in sources:
            file_path = root / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                if filename == "SOUL.md" and self._is_template_content(
                    content,
                    "legacy/SOUL.md",
                ):
                    content = load_bundled_template("SOUL.md") or content
                if not content.strip():
                    continue
                if filename in self._SKIPPABLE_DEFAULTS and self._is_template_content(
                    content, filename
                ):
                    continue
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    @staticmethod
    def _is_template_content(content: str, template_path: str) -> bool:
        """Check if *content* is identical to the bundled template (user hasn't customized it)."""
        tpl = load_bundled_template(template_path)
        if tpl is not None:
            return content.strip() == tpl.strip()
        return False

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        *,
        media: list[str] | None = None,
        channel: str | None = None,
        current_role: str = "user",
        session_summary: str | None = None,
        runtime_context_blocks: Sequence[RuntimeContextBlock] | None = None,
        workspace: Path | None = None,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        unified_session: bool = False,
        capability_mode: str = "full",
        skill_names: Sequence[str] | None = None,
        allowed_tool_names: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        root = workspace or self.workspace
        active_skill_names = (
            self.skills.get_explicitly_invoked_skills(current_message)
            if current_role == "user" and capability_mode != "chat_only"
            else []
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    active_skill_names=active_skill_names,
                    channel=channel,
                    session_summary=session_summary,
                    workspace=root,
                    include_memory_recent_history=include_memory_recent_history,
                    session_key=session_key,
                    unified_session=unified_session,
                    capability_mode=capability_mode,
                    skill_names=skill_names,
                    allowed_tool_names=allowed_tool_names,
                ),
            },
            *history,
        ]
        current = self.build_current_message(
            current_message,
            media=media,
            current_role=current_role,
            runtime_context_blocks=runtime_context_blocks,
        )
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(
                last.get("content"),
                current.get("content"),
            )
            current_meta = current.get("_meta")
            if current_role == "user" and isinstance(current_meta, dict):
                internal_meta = dict(last.get("_meta") or {})
                internal_meta.update(cast(dict[str, Any], current_meta))
                last["_meta"] = internal_meta
            messages[-1] = last
            return messages
        messages.append(current)
        return messages

    def build_current_message(
        self,
        current_message: str,
        *,
        media: list[str] | None = None,
        current_role: str = "user",
        runtime_context_blocks: Sequence[RuntimeContextBlock] | None = None,
    ) -> dict[str, Any]:
        """Build only the fresh turn message without merging it into history."""
        content = self.build_user_content(current_message, image_paths=media)
        blocks = list(runtime_context_blocks or ()) if current_role == "user" else []
        merged, runtime_context_meta = append_runtime_context(content, blocks)
        current: dict[str, Any] = {"role": current_role, "content": merged}
        if current_role == "user" and runtime_context_meta is not None:
            current["_meta"] = {
                RUNTIME_CONTEXT_MESSAGE_META: runtime_context_meta,
            }
        return current

    def build_user_content(
        self,
        text: str,
        image_paths: list[str] | None,
    ) -> str | list[dict[str, Any]]:
        """Build user message content from prefiltered image paths."""
        if not image_paths:
            return text

        image_blocks: list[dict[str, Any]] = []
        for path in image_paths:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            # Re-detect from the bytes used for the request: the file may have
            # changed since attachment routing, and the data URL needs its MIME.
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            image_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not image_blocks:
            return text
        return image_blocks + [{"type": "text", "text": text}]
