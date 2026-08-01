"""Persistent Codex CLI job orchestration for long-running code tasks."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.utils.helpers import ensure_dir, timestamp

_ACTIVE_STATUSES = {"starting", "running", "resuming"}
_UUID_CHARS = set("0123456789abcdef-")


@dataclass
class CodexJob:
    """Persisted state for one Codex CLI execution."""

    job_id: str
    task: str
    cwd: str
    origin_channel: str
    origin_chat_id: str
    session_key: str
    resume_key: str | None = None
    acceptance: list[str] | None = None
    status: str = "starting"
    created_at: str = ""
    updated_at: str = ""
    attempt: int = 0
    session_id: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    last_error: str | None = None
    last_event_at: str | None = None
    completion_announced: bool = False

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = timestamp()
        if not self.updated_at:
            self.updated_at = self.created_at

    @property
    def is_active(self) -> bool:
        return self.status in _ACTIVE_STATUSES


class CodexJobManager:
    """Manage long-running Codex CLI jobs outside the main agent loop."""

    _STALL_SECONDS = 300
    _MONITOR_POLL_SECONDS = 2.0
    _MAX_RESULT_CHARS = 12_000

    def __init__(self, workspace: Path, bus: MessageBus):
        self.workspace = workspace
        self.bus = bus
        self.jobs_dir = ensure_dir(workspace / ".nanobot" / "codex-jobs")
        self._lock = asyncio.Lock()
        self._monitors: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._restored = False

    async def delegate(
        self,
        *,
        task: str,
        cwd: str | None,
        origin_channel: str,
        origin_chat_id: str,
        session_key: str,
        resume_key: str | None = None,
        acceptance: list[str] | None = None,
    ) -> str:
        """Start a new Codex job or reuse/resume an existing logical task."""
        await self.restore_pending_jobs()
        cwd_path = Path(cwd or self.workspace).expanduser().resolve()
        acceptance = [item.strip() for item in (acceptance or []) if item and item.strip()]
        prompt = self._build_prompt(task, acceptance)

        async with self._lock:
            existing = self._resolve_job(job_id=None, resume_key=resume_key)
            if existing is not None:
                if existing.is_active and self._job_pid_running(existing):
                    self._ensure_monitor(existing)
                    return (
                        f"Codex job `{existing.job_id}` is already running"
                        + (f" for `{resume_key}`." if resume_key else ".")
                    )
                if existing.status == "completed":
                    return (
                        f"Codex job `{existing.job_id}` already completed."
                        " Use `codex_status` to inspect the result or choose a new resume_key."
                    )
                if existing.session_id:
                    await self._launch_job(existing, self._build_resume_prompt(existing, None), resume=True)
                    return (
                        f"Resumed Codex job `{existing.job_id}`"
                        + (f" for `{resume_key}`." if resume_key else ".")
                    )

                existing.task = task
                existing.cwd = str(cwd_path)
                existing.origin_channel = origin_channel
                existing.origin_chat_id = origin_chat_id
                existing.session_key = session_key
                existing.acceptance = acceptance or None
                await self._launch_job(existing, prompt, resume=False)
                return (
                    f"Restarted Codex job `{existing.job_id}`"
                    + (f" for `{resume_key}`." if resume_key else ".")
                )

            job = CodexJob(
                job_id=uuid.uuid4().hex[:12],
                task=task,
                cwd=str(cwd_path),
                origin_channel=origin_channel,
                origin_chat_id=origin_chat_id,
                session_key=session_key,
                resume_key=resume_key,
                acceptance=acceptance or None,
            )
            self._write_spec(job)
            self._save_job(job)
            await self._launch_job(job, prompt, resume=False)
            return (
                f"Started Codex job `{job.job_id}`"
                + (f" for `{resume_key}`." if resume_key else ".")
                + " Use `codex_status` to check progress."
            )

    async def resume(
        self,
        *,
        job_id: str | None = None,
        resume_key: str | None = None,
        prompt: str | None = None,
        origin_channel: str | None = None,
        origin_chat_id: str | None = None,
        session_key: str | None = None,
    ) -> str:
        """Resume an interrupted or failed Codex job."""
        await self.restore_pending_jobs()
        async with self._lock:
            job = self._resolve_job(job_id=job_id, resume_key=resume_key)
            if job is None:
                return self._not_found(job_id, resume_key)
            if job.is_active and self._job_pid_running(job):
                self._ensure_monitor(job)
                return f"Codex job `{job.job_id}` is already running."

            if origin_channel:
                job.origin_channel = origin_channel
            if origin_chat_id:
                job.origin_chat_id = origin_chat_id
            if session_key:
                job.session_key = session_key

            resume_prompt = self._build_resume_prompt(job, prompt)
            await self._launch_job(job, resume_prompt, resume=bool(job.session_id))
            action = "Resumed" if job.session_id else "Restarted"
            return f"{action} Codex job `{job.job_id}`."

    async def status(self, *, job_id: str | None = None, resume_key: str | None = None) -> str:
        """Return a human-readable status summary for a job."""
        await self.restore_pending_jobs()
        async with self._lock:
            job = self._resolve_job(job_id=job_id, resume_key=resume_key)
            if job is None:
                return self._not_found(job_id, resume_key)

            if job.is_active and not self._job_pid_running(job):
                job.status = "interrupted"
                job.updated_at = timestamp()
                self._save_job(job)

            tail = self._read_last_message(job)
            parts = [
                f"Codex job `{job.job_id}`",
                f"status={self._display_status(job)}",
                f"attempt={job.attempt}",
                f"cwd={job.cwd}",
            ]
            if job.resume_key:
                parts.append(f"resume_key={job.resume_key}")
            if job.session_id:
                parts.append(f"session_id={job.session_id}")
            if job.last_error:
                parts.append(f"last_error={job.last_error}")
            if tail:
                parts.append(f"last_message={tail}")
            return "\n".join(parts)

    async def restore_pending_jobs(self) -> None:
        """Reattach lightweight monitors for active jobs after process restarts."""
        async with self._lock:
            if self._restored:
                return
            self._restored = True
            for job in self._load_all_jobs():
                if not job.is_active:
                    continue
                if self._job_pid_running(job):
                    self._ensure_monitor(job, process=None, resumed=True)
                else:
                    job.status = "interrupted"
                    job.updated_at = timestamp()
                    self._save_job(job)

    async def close(self) -> None:
        """Stop in-process monitors without terminating Codex child processes."""
        tasks = list(self._monitors.values())
        self._monitors.clear()
        self._processes.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _job_dir(self, job_id: str) -> Path:
        return ensure_dir(self.jobs_dir / job_id)

    def _state_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "state.json"

    def _spec_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "spec.md"

    def _events_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "events.jsonl"

    def _stderr_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "stderr.log"

    def _last_message_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "last.txt"

    def _save_job(self, job: CodexJob) -> None:
        self._state_path(job.job_id).write_text(
            json.dumps(asdict(job), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_job(self, job_id: str) -> CodexJob | None:
        path = self._state_path(job_id)
        if not path.exists():
            return None
        try:
            return CodexJob(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            logger.exception("Failed to load Codex job state {}", job_id)
            return None

    def _load_all_jobs(self) -> list[CodexJob]:
        jobs: list[CodexJob] = []
        for state_path in self.jobs_dir.glob("*/state.json"):
            try:
                jobs.append(CodexJob(**json.loads(state_path.read_text(encoding="utf-8"))))
            except Exception:
                logger.exception("Failed to read Codex job state from {}", state_path)
        jobs.sort(key=lambda item: item.updated_at, reverse=True)
        return jobs

    def _resolve_job(self, job_id: str | None, resume_key: str | None) -> CodexJob | None:
        if job_id:
            return self._load_job(job_id)
        if resume_key:
            for job in self._load_all_jobs():
                if job.resume_key == resume_key:
                    return job
        return None

    def _ensure_monitor(
        self,
        job: CodexJob,
        *,
        process: asyncio.subprocess.Process | None = None,
        resumed: bool = False,
    ) -> None:
        if process is not None:
            self._processes[job.job_id] = process
        if job.job_id in self._monitors:
            return
        self._monitors[job.job_id] = asyncio.create_task(
            self._monitor_job(job.job_id, resumed=resumed)
        )

    async def _launch_job(self, job: CodexJob, prompt: str, *, resume: bool) -> None:
        ensure_dir(Path(job.cwd))
        job.attempt += 1
        job.status = "resuming" if resume and job.session_id else "starting"
        job.updated_at = timestamp()
        job.last_error = None
        job.exit_code = None
        job.completion_announced = False
        self._save_job(job)

        command = self._build_command(job, prompt, resume=resume and bool(job.session_id))
        process = await self._create_process(command, cwd=Path(job.cwd))
        job.pid = process.pid
        job.status = "running"
        job.updated_at = timestamp()
        self._save_job(job)
        self._ensure_monitor(job, process=process)

    async def _create_process(
        self,
        command: list[str],
        *,
        cwd: Path,
    ) -> asyncio.subprocess.Process:
        logger.info("Launching Codex job in {}: {}", cwd, command)
        return await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    def _build_command(self, job: CodexJob, prompt: str, *, resume: bool) -> list[str]:
        last_path = str(self._last_message_path(job.job_id))
        base = self._codex_prefix(job.cwd)
        if resume and job.session_id:
            return [
                *base,
                "exec",
                "resume",
                "--skip-git-repo-check",
                "--full-auto",
                "--json",
                "-o",
                last_path,
                job.session_id,
                prompt,
            ]
        return [
            *base,
            "exec",
            "--skip-git-repo-check",
            "--full-auto",
            "--json",
            "--color",
            "never",
            "-o",
            last_path,
            prompt,
        ]

    @staticmethod
    def _codex_prefix(cwd: str) -> list[str]:
        if os.name == "nt":
            return ["cmd", "/d", "/c", "codex", "-C", cwd]
        return ["codex", "-C", cwd]

    async def _monitor_job(self, job_id: str, *, resumed: bool = False) -> None:
        process = self._processes.get(job_id)
        try:
            if process is not None:
                await self._capture_live_process(job_id, process)
            else:
                await self._poll_external_process(job_id, resumed=resumed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Codex monitor crashed for {}", job_id)
            async with self._lock:
                job = self._load_job(job_id)
                if job is not None:
                    job.status = "failed"
                    job.last_error = "Codex monitor crashed"
                    job.updated_at = timestamp()
                    self._save_job(job)
        finally:
            self._monitors.pop(job_id, None)
            self._processes.pop(job_id, None)

    async def _capture_live_process(
        self,
        job_id: str,
        process: asyncio.subprocess.Process,
    ) -> None:
        stdout_task = asyncio.create_task(
            self._drain_stream(job_id, process.stdout, target="stdout")
        )
        stderr_task = asyncio.create_task(
            self._drain_stream(job_id, process.stderr, target="stderr")
        )
        try:
            exit_code = await process.wait()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            await self._finalize_job(job_id, exit_code=exit_code)
        finally:
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    async def _poll_external_process(self, job_id: str, *, resumed: bool = False) -> None:
        while True:
            async with self._lock:
                job = self._load_job(job_id)
            if job is None:
                return
            if not job.pid or not self._job_pid_running(job):
                await self._finalize_job(job_id, exit_code=None if resumed else job.exit_code)
                return
            await asyncio.sleep(self._MONITOR_POLL_SECONDS)

    async def _drain_stream(
        self,
        job_id: str,
        stream: asyncio.StreamReader | None,
        *,
        target: str,
    ) -> None:
        if stream is None:
            return
        path = self._events_path(job_id) if target == "stdout" else self._stderr_path(job_id)
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text)
            async with self._lock:
                job = self._load_job(job_id)
                if job is None:
                    continue
                job.last_event_at = timestamp()
                maybe_session_id = self._extract_session_id(text)
                if maybe_session_id and not job.session_id:
                    job.session_id = maybe_session_id
                self._save_job(job)

    async def _finalize_job(self, job_id: str, *, exit_code: int | None) -> None:
        async with self._lock:
            job = self._load_job(job_id)
            if job is None:
                return
            final_message = self._read_last_message(job)
            job.exit_code = exit_code
            if exit_code == 0 or (exit_code is None and final_message):
                job.status = "completed"
            elif final_message:
                job.status = "failed"
                job.last_error = f"Codex exited with code {exit_code}" if exit_code is not None else "Codex ended unexpectedly"
            else:
                job.status = "interrupted" if exit_code is None else "failed"
                if exit_code is not None:
                    job.last_error = f"Codex exited with code {exit_code}"
                elif not job.last_error:
                    job.last_error = "Codex stopped before producing a final message"
            job.updated_at = timestamp()
            self._save_job(job)
            should_announce = not job.completion_announced
        if should_announce:
            await self._announce_completion(job_id)

    async def _announce_completion(self, job_id: str) -> None:
        async with self._lock:
            job = self._load_job(job_id)
            if job is None or job.completion_announced:
                return
            result = self._read_last_message(job) or job.last_error or "No final message captured."
            result = result[: self._MAX_RESULT_CHARS]
            job.completion_announced = True
            job.updated_at = timestamp()
            self._save_job(job)

        status_text = "completed successfully" if job.status == "completed" else job.status
        content = (
            f"[Codex job {status_text}]\n\n"
            f"Task: {job.task}\n"
            f"Working directory: {job.cwd}\n"
            f"Attempt: {job.attempt}\n"
            f"Result:\n{result}\n\n"
            "Summarize this naturally for the user in 1-2 sentences. "
            "Do not mention internal job IDs unless the user asks."
        )
        await self.bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="codex_job",
                chat_id=f"{job.origin_channel}:{job.origin_chat_id}",
                content=content,
                metadata={"codex_job_id": job.job_id},
                session_key_override=job.session_key,
            )
        )

    def _write_spec(self, job: CodexJob) -> None:
        lines = [
            "# Codex Job",
            "",
            f"- Job ID: {job.job_id}",
            f"- Created At: {job.created_at}",
            f"- Working Directory: {job.cwd}",
        ]
        if job.resume_key:
            lines.append(f"- Resume Key: {job.resume_key}")
        lines.extend(["", "## Task", "", job.task])
        if job.acceptance:
            lines.extend(["", "## Acceptance Criteria", ""])
            lines.extend(f"- {item}" for item in job.acceptance)
        self._spec_path(job.job_id).write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _build_prompt(task: str, acceptance: list[str]) -> str:
        lines = [
            task.strip(),
            "",
            "Work directly in the repository and finish the implementation end-to-end.",
            "When you are done, summarize the files you changed and any validation you ran.",
        ]
        if acceptance:
            lines.extend(["", "Acceptance criteria:"])
            lines.extend(f"- {item}" for item in acceptance)
        return "\n".join(lines).strip()

    @staticmethod
    def _build_resume_prompt(job: CodexJob, extra_prompt: str | None) -> str:
        lines = [
            "Resume the existing task in the current workspace state.",
            "Do not restart from scratch.",
            "Inspect the current diff and continue until the task is complete.",
            "When finished, summarize changed files and validation.",
        ]
        if extra_prompt and extra_prompt.strip():
            lines.extend(["", extra_prompt.strip()])
        elif job.acceptance:
            lines.extend(["", "Acceptance criteria:"])
            lines.extend(f"- {item}" for item in job.acceptance)
        return "\n".join(lines)

    @classmethod
    def _display_status(cls, job: CodexJob) -> str:
        if job.status == "running" and job.last_event_at:
            try:
                last = datetime.fromisoformat(job.last_event_at)
                if (datetime.now() - last).total_seconds() > cls._STALL_SECONDS:
                    return "stalled"
            except ValueError:
                pass
        return job.status

    def _read_last_message(self, job: CodexJob) -> str:
        path = self._last_message_path(job.job_id)
        if not path.exists():
            return ""
        try:
            content = path.read_text(encoding="utf-8").strip()
        except Exception:
            logger.exception("Failed to read final Codex output for {}", job.job_id)
            return ""
        if len(content) <= 400:
            return content
        return content[:400] + "..."

    @staticmethod
    def _extract_session_id(line: str) -> str | None:
        try:
            data = json.loads(line)
        except Exception:
            return None
        return CodexJobManager._find_session_token(data)

    @classmethod
    def _find_session_token(cls, value: Any) -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                if "session" in key.lower() or "conversation" in key.lower() or "thread" in key.lower():
                    token = cls._coerce_session_token(item)
                    if token:
                        return token
                nested = cls._find_session_token(item)
                if nested:
                    return nested
        elif isinstance(value, list):
            for item in value:
                nested = cls._find_session_token(item)
                if nested:
                    return nested
        return None

    @staticmethod
    def _coerce_session_token(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if len(text) < 3:
            return None
        lower = text.lower()
        if all(ch in _UUID_CHARS for ch in lower):
            return text
        return text

    def _job_pid_running(self, job: CodexJob) -> bool:
        process = self._processes.get(job.job_id)
        if process is not None:
            return process.returncode is None
        if not job.pid:
            return False
        return self._pid_running(job.pid)

    @staticmethod
    def _pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name != "nt":
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return True

        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    @staticmethod
    def _not_found(job_id: str | None, resume_key: str | None) -> str:
        if job_id:
            return f"Error: Codex job `{job_id}` not found."
        if resume_key:
            return f"Error: No Codex job found for resume_key `{resume_key}`."
        return "Error: Provide either job_id or resume_key."
