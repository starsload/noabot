"""Egress trim of stale bulk tool results (noabot moderation fix).

Live incident: accumulated web scrapes and ops dumps in the shared session
replay eventually tripped Aliyun MaaS input moderation deterministically,
blocking every turn.  Stale bulk payloads must not reach the provider, while
persistence, the freshest results, and short tool outputs stay intact.
"""

from __future__ import annotations

from nanobot.providers.base import LLMProvider

_LONG = "page text " + "x" * 600


def _tool(idx: int, name: str = "web_search", content: str = _LONG) -> dict:
    return {"role": "tool", "tool_call_id": f"call_{idx}", "name": name, "content": content}


def _build(n: int, name: str = "web_search", content: str = _LONG) -> list[dict]:
    messages: list[dict] = []
    for i in range(n):
        messages.append({"role": "user", "content": f"msg {i}"})
        messages.append(_tool(i, name, content))
    return messages


def test_stale_web_results_trimmed_all_names() -> None:
    trimmed = LLMProvider.trim_stale_tool_results(_build(12))
    tools = [m for m in trimmed if m.get("role") == "tool"]
    stale = tools[: -LLMProvider._TOOL_TRIM_KEEP_RECENT]
    fresh = tools[-LLMProvider._TOOL_TRIM_KEEP_RECENT:]
    assert all(m["content"] == LLMProvider._TOOL_TRIM_PLACEHOLDER for m in stale)
    assert all(m["content"] == _LONG for m in fresh)


def test_trimmed_output_carries_no_original_text() -> None:
    messages = _build(12)
    messages[1]["content"] = "sensitive-spam-page-body " + "y" * 600
    trimmed = LLMProvider.trim_stale_tool_results(messages)
    flat = "\n".join(str(m.get("content", "")) for m in trimmed)
    assert "sensitive-spam-page-body" not in flat


def test_short_non_web_tool_results_survive_anywhere() -> None:
    messages = _build(12, name="exec", content="ok")
    assert LLMProvider.trim_stale_tool_results(messages) == messages


def test_stale_long_non_web_tool_results_trimmed() -> None:
    messages = _build(12, name="exec")
    trimmed = LLMProvider.trim_stale_tool_results(messages)
    tools = [m for m in trimmed if m.get("role") == "tool"]
    stale = tools[: -LLMProvider._TOOL_TRIM_KEEP_RECENT]
    fresh = tools[-LLMProvider._TOOL_TRIM_KEEP_RECENT:]
    assert all(m["content"] == LLMProvider._TOOL_TRIM_PLACEHOLDER for m in stale)
    assert all(m["content"] == _LONG for m in fresh)


def test_short_history_returned_unchanged() -> None:
    messages = _build(LLMProvider._TOOL_TRIM_KEEP_RECENT)
    assert LLMProvider.trim_stale_tool_results(messages) == messages


def test_input_list_not_mutated() -> None:
    messages = _build(12)
    original_first = messages[1]["content"]
    LLMProvider.trim_stale_tool_results(messages)
    assert messages[1]["content"] == original_first


def test_tool_name_kept_for_continuity() -> None:
    messages = _build(12, name="web_fetch")
    trimmed = LLMProvider.trim_stale_tool_results(messages)
    tools = [m for m in trimmed if m.get("role") == "tool"]
    stale = tools[: -LLMProvider._TOOL_TRIM_KEEP_RECENT]
    assert all(m.get("name") == "web_fetch" for m in stale)
    assert all(m["content"] == LLMProvider._TOOL_TRIM_PLACEHOLDER for m in stale)
