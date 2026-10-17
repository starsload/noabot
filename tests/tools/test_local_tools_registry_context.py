"""noabot regression: locally re-implemented tools must satisfy the upstream
ContextAware protocol through the real ToolRegistry path.

The 2026-10-17 production crash: cc_delegate kept dev-clean's
set_context(channel, chat_id) while registry.prepare_call passes ONE
RequestContext — every call raised TypeError and killed the turn. Unit tests
called delegate() directly and never exercised the registry, which is how it
survived the full suite. These tests pin the protocol conformance itself.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nanobot.agent.tools.claude_code import (
    CCDelegateTool,
    CCResumeTool,
    CCStatusTool,
)
from nanobot.agent.tools.codex import (
    CodexDelegateTool,
    CodexResumeTool,
    CodexStatusTool,
)
from nanobot.agent.tools.context import RequestContext, request_context
from nanobot.agent.tools.noah_local_painter import NoahLocalPainterTool
from nanobot.agent.tools.registry import ToolRegistry


def _ctx() -> RequestContext:
    return RequestContext(
        channel="qq_personal", chat_id="641745039", message_id="mid-1",
        session_key="qq_personal:641745039",
    )


def _registry(tool) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(tool)
    return reg


def _prepare_ok(reg: ToolRegistry, name: str, params: dict):
    with request_context(_ctx()):
        tool, _params, error = reg.prepare_call(name, params)
    assert error is None, f"prepare_call failed: {error}"
    assert tool is not None
    return tool


@pytest.mark.parametrize(
    ("factory", "name", "params"),
    [
        (lambda: CCDelegateTool(SimpleNamespace()), "cc_delegate", {"task": "do work"}),
        (lambda: CCStatusTool(SimpleNamespace()), "cc_status", {}),
        (lambda: CCResumeTool(SimpleNamespace()), "cc_resume", {"job_id": "job1", "prompt": "go"}),
        (lambda: CodexDelegateTool(SimpleNamespace()), "codex_delegate", {"task": "do work"}),
        (lambda: CodexStatusTool(SimpleNamespace()), "codex_status", {}),
        (lambda: CodexResumeTool(SimpleNamespace()), "codex_resume", {"job_id": "job1", "prompt": "go"}),
    ],
    ids=["cc_delegate", "cc_status", "cc_resume", "codex_delegate", "codex_status", "codex_resume"],
)
def test_job_tools_survive_registry_prepare(factory, name, params) -> None:
    tool = _prepare_ok(_registry(factory()), name, params)
    # routing fields must carry the request context, not the stale defaults
    assert tool._origin_channel == "qq_personal"
    assert tool._origin_chat_id == "641745039"
    assert tool._session_key == "qq_personal:641745039"


def test_painter_survives_registry_prepare(tmp_path) -> None:
    painter = NoahLocalPainterTool(workspace=tmp_path, send_callback=None)
    tool = _prepare_ok(_registry(painter), "noah_local_painter", {"action": "health_check"})
    assert tool._default_channel == "qq_personal"
    assert tool._default_chat_id == "641745039"
    assert tool._default_message_id == "mid-1"


def test_job_tools_fall_back_without_session_key() -> None:
    tool = CCDelegateTool(SimpleNamespace())
    tool.set_context(RequestContext(channel="discord", chat_id="c1"))
    assert tool._session_key == "discord:c1"
