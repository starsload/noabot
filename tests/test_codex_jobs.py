from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.codex_jobs import CodexJob, CodexJobManager
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus


class _FakeStream:
    def __init__(self, lines: list[str] | None = None) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        for line in lines or []:
            self.feed(line)

    def feed(self, line: str) -> None:
        self._queue.put_nowait(line.encode("utf-8"))

    def finish(self) -> None:
        self._queue.put_nowait(b"")

    async def readline(self) -> bytes:
        return await self._queue.get()


class _FakeProcess:
    def __init__(self, *, pid: int = 4321, stdout_lines: list[str] | None = None) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream()
        self._done = asyncio.Event()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, code: int = 0) -> None:
        self.returncode = code
        self.stdout.finish()
        self.stderr.finish()
        self._done.set()


@pytest.mark.asyncio
async def test_delegate_starts_job_and_announces_completion(tmp_path) -> None:
    bus = MessageBus()
    manager = CodexJobManager(workspace=tmp_path, bus=bus)
    process = _FakeProcess(stdout_lines=['{"type":"session.started","session_id":"sess-1"}\n'])

    async def fake_create_process(command, *, cwd):
        return process

    manager._create_process = fake_create_process  # type: ignore[method-assign]

    result = await manager.delegate(
        task="Implement retry handling",
        cwd=str(tmp_path),
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        resume_key="retry-task",
        acceptance=["Update code", "Add tests"],
    )

    assert "Started Codex job" in result
    job = manager._load_all_jobs()[0]
    manager._last_message_path(job.job_id).write_text("Implemented fix and added tests.", encoding="utf-8")

    process.finish(0)
    await asyncio.wait_for(manager._monitors[job.job_id], timeout=1.0)

    inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
    saved = manager._load_job(job.job_id)

    assert inbound.sender_id == "codex_job"
    assert saved is not None
    assert saved.status == "completed"
    assert saved.session_id == "sess-1"


@pytest.mark.asyncio
async def test_delegate_reuses_running_job_for_same_resume_key(tmp_path) -> None:
    bus = MessageBus()
    manager = CodexJobManager(workspace=tmp_path, bus=bus)
    process = _FakeProcess()
    calls: list[list[str]] = []

    async def fake_create_process(command, *, cwd):
        calls.append(command)
        return process

    manager._create_process = fake_create_process  # type: ignore[method-assign]

    await manager.delegate(
        task="Implement feature A",
        cwd=str(tmp_path),
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        resume_key="feature-a",
        acceptance=None,
    )
    second = await manager.delegate(
        task="Implement feature A",
        cwd=str(tmp_path),
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        resume_key="feature-a",
        acceptance=None,
    )

    assert "already running" in second
    assert len(calls) == 1

    await manager.close()


@pytest.mark.asyncio
async def test_resume_uses_saved_session_id(tmp_path) -> None:
    bus = MessageBus()
    manager = CodexJobManager(workspace=tmp_path, bus=bus)
    created = CodexJob(
        job_id="job123",
        task="Finish provider refactor",
        cwd=str(tmp_path),
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        resume_key="provider-refactor",
        status="interrupted",
        session_id="session-123",
    )
    manager._save_job(created)
    manager._write_spec(created)

    process = _FakeProcess()
    captured: list[list[str]] = []

    async def fake_create_process(command, *, cwd):
        captured.append(command)
        return process

    manager._create_process = fake_create_process  # type: ignore[method-assign]

    result = await manager.resume(job_id="job123", prompt="Continue from the existing diff.")

    assert "Resumed Codex job" in result
    assert captured
    prefix = manager._codex_prefix(str(tmp_path))
    assert captured[0][: len(prefix)] == prefix
    assert "resume" in captured[0]
    assert "session-123" in captured[0]

    await manager.close()


@pytest.mark.asyncio
async def test_status_marks_missing_running_pid_as_interrupted(tmp_path, monkeypatch) -> None:
    bus = MessageBus()
    manager = CodexJobManager(workspace=tmp_path, bus=bus)
    job = CodexJob(
        job_id="job456",
        task="Refactor worker",
        cwd=str(tmp_path),
        origin_channel="cli",
        origin_chat_id="direct",
        session_key="cli:direct",
        status="running",
        pid=999,
    )
    manager._save_job(job)

    monkeypatch.setattr(manager, "_pid_running", lambda _pid: False)
    status = await manager.status(job_id="job456")

    assert "status=interrupted" in status


@pytest.mark.asyncio
async def test_loop_treats_codex_job_system_messages_as_assistant(tmp_path) -> None:
    from nanobot.agent.loop import AgentLoop

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    with patch("nanobot.agent.loop.ContextBuilder") as mock_context_cls, \
         patch("nanobot.agent.loop.SubagentManager"), \
         patch("nanobot.agent.loop.CodexJobManager") as mock_codex_manager_cls:
        mock_codex_manager_cls.return_value.restore_pending_jobs = AsyncMock(return_value=None)
        mock_codex_manager_cls.return_value.close = AsyncMock(return_value=None)
        context = mock_context_cls.return_value
        context.build_messages.return_value = []

        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)
        loop._run_agent_loop = AsyncMock(return_value=("done", [], []))

        msg = InboundMessage(
            channel="system",
            sender_id="codex_job",
            chat_id="cli:direct",
            content="job finished",
        )
        await loop._process_message(msg)

        assert context.build_messages.call_args.kwargs["current_role"] == "assistant"
