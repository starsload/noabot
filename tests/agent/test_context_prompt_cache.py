"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

import datetime as datetime_module
from datetime import datetime as real_datetime
from importlib.resources import files as pkg_files
from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.runtime_context import RuntimeContextBlock


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_bootstrap_files_are_backed_by_templates() -> None:
    template_dir = pkg_files("nanobot") / "templates"

    for filename in ContextBuilder.BOOTSTRAP_FILES:
        assert (template_dir / filename).is_file(), f"missing bootstrap template: {filename}"


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_runtime_context_is_separate_untrusted_user_message(tmp_path) -> None:
    """Runtime metadata should be merged with the user message."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="hello world",
        channel="cli",
        runtime_context_blocks=[
            RuntimeContextBlock(source="test", content="provider context"),
        ],
    )

    content = messages[-1]["content"]
    user_pos = content.find("hello world")
    context_pos = content.find("provider context")
    assert user_pos < context_pos, "user content must precede provider context"


def test_runtime_context_includes_speaker_identity_and_owner_status(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    # Speaker identity reaches the model as a runtime-context provider block
    # (nanobot.agent.loop.AgentLoop._speaker_runtime_context builds this).
    runtime = ContextBuilder._build_runtime_context(
        "qq",
        "group123",
        sender_id="user1",
        sender_name="Alice",
        sender_username="alice",
        conversation_type="group",
        is_owner=False,
    )
    messages = builder.build_messages(
        history=[],
        current_message="hello",
        channel="qq",
        runtime_context_blocks=[RuntimeContextBlock(source="speaker", content=runtime)],
    )

    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert "Conversation Type: group" in user_content
    assert "Speaker ID: user1" in user_content
    assert "Speaker Name: Alice" in user_content
    assert "Speaker Username: alice" in user_content
    assert "Is Owner: false" in user_content
    assert "Current speaker may not be the workspace owner from USER.md." in user_content


def test_system_prompt_clarifies_owner_vs_current_speaker(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "workspace owner" in prompt
    assert "current speaker" in prompt
    assert "Is Owner: false" in prompt
    assert "Is Owner: true" in prompt
