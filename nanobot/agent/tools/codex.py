"""Tools for delegating long-running implementation work to Codex CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.codex_jobs import CodexJobManager


class _CodexToolBase(Tool):
    """Shared context routing for Codex job tools."""

    def __init__(self, manager: "CodexJobManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = f"{channel}:{chat_id}"


class CodexDelegateTool(_CodexToolBase):
    """Start or reuse a background Codex implementation job."""

    @property
    def name(self) -> str:
        return "codex_delegate"

    @property
    def description(self) -> str:
        return (
            "Run a long-lived coding task through Codex CLI in the background. "
            "Use this when implementation may take longer than a normal tool call."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Implementation task for Codex to complete",
                    "minLength": 5,
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory for Codex. Defaults to the current workspace.",
                },
                "resume_key": {
                    "type": "string",
                    "description": "Stable logical key for deduplicating and resuming the same task.",
                    "minLength": 3,
                },
                "acceptance": {
                    "type": "array",
                    "description": "Optional acceptance criteria that Codex should satisfy before finishing.",
                    "items": {"type": "string"},
                },
            },
            "required": ["task"],
        }

    async def execute(
        self,
        task: str,
        cwd: str | None = None,
        resume_key: str | None = None,
        acceptance: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        return await self._manager.delegate(
            task=task,
            cwd=cwd,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
            resume_key=resume_key,
            acceptance=acceptance,
        )


class CodexStatusTool(_CodexToolBase):
    """Inspect the status of an existing Codex job."""

    @property
    def name(self) -> str:
        return "codex_status"

    @property
    def description(self) -> str:
        return "Check the status or final result of a background Codex CLI job."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Concrete Codex job id to inspect",
                    "minLength": 3,
                },
                "resume_key": {
                    "type": "string",
                    "description": "Logical task key to inspect when job_id is not known",
                    "minLength": 3,
                },
            },
        }

    async def execute(
        self,
        job_id: str | None = None,
        resume_key: str | None = None,
        **kwargs: Any,
    ) -> str:
        return await self._manager.status(job_id=job_id, resume_key=resume_key)


class CodexResumeTool(_CodexToolBase):
    """Resume a previously interrupted Codex job."""

    @property
    def name(self) -> str:
        return "codex_resume"

    @property
    def description(self) -> str:
        return (
            "Resume or restart an interrupted Codex CLI job without losing track of the logical task."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Concrete Codex job id to resume",
                    "minLength": 3,
                },
                "resume_key": {
                    "type": "string",
                    "description": "Logical task key to resume when job_id is not known",
                    "minLength": 3,
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional extra instruction for the resumed run",
                },
            },
        }

    async def execute(
        self,
        job_id: str | None = None,
        resume_key: str | None = None,
        prompt: str | None = None,
        **kwargs: Any,
    ) -> str:
        return await self._manager.resume(
            job_id=job_id,
            resume_key=resume_key,
            prompt=prompt,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
        )


class CodexTool(Tool):
    """Placeholder tool for Codex integration (requires manager for full functionality)."""

    def __init__(self, workspace: Any):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "codex"

    @property
    def description(self) -> str:
        return "Codex CLI integration (placeholder - not yet configured)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "Codex tool is not fully configured. A job manager is required for delegation."
