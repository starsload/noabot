from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanobot.providers.custom_provider import CustomProvider


def _chat_completion_response(
    content: str | None = "Hello",
    *,
    finish_reason: str = "stop",
    tool_calls: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls or []),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8),
    )


def _responses_response(
    *,
    output_text: str = "Hello from responses",
    output: list | None = None,
    status: str = "completed",
) -> SimpleNamespace:
    return SimpleNamespace(
        error=None,
        output=output or [],
        output_text=output_text,
        status=status,
        usage=SimpleNamespace(input_tokens=4, output_tokens=6, total_tokens=10),
    )


@pytest.mark.asyncio
async def test_custom_provider_chat_completions_mode_stays_unchanged() -> None:
    provider = CustomProvider(api_key="test-key", api_mode="chat_completions")
    completions_create = AsyncMock(return_value=_chat_completion_response())
    responses_create = AsyncMock()
    provider._client.chat.completions.create = completions_create
    provider._client.responses.create = responses_create

    result = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert result.content == "Hello"
    assert result.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 5,
        "total_tokens": 8,
    }
    completions_create.assert_awaited_once()
    responses_create.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_provider_responses_mode_converts_forced_tool_choice() -> None:
    provider = CustomProvider(api_key="test-key", api_mode="responses")
    responses_create = AsyncMock(
        return_value=_responses_response(
            output_text="",
            output=[
                SimpleNamespace(
                    type="function_call",
                    call_id="call_save",
                    id="fc_save",
                    name="save_memory",
                    arguments='{"history_entry":"h","memory_update":"m"}',
                )
            ],
        )
    )
    provider._client.responses.create = responses_create

    tools = [
        {
            "type": "function",
            "function": {
                "name": "save_memory",
                "description": "Persist memory",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    forced_tool_choice = {"type": "function", "function": {"name": "save_memory"}}

    result = await provider.chat(
        messages=[{"role": "user", "content": "remember this"}],
        tools=tools,
        tool_choice=forced_tool_choice,
        reasoning_effort="medium",
    )

    kwargs = responses_create.await_args.kwargs
    assert kwargs["max_output_tokens"] == 4096
    assert kwargs["reasoning"] == {"effort": "medium"}
    assert kwargs["tool_choice"] == {"type": "function", "name": "save_memory"}
    assert kwargs["tools"] == [
        {
            "type": "function",
            "name": "save_memory",
            "description": "Persist memory",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    assert result.content is None
    assert result.tool_calls[0].id == "call_save|fc_save"
    assert result.tool_calls[0].arguments == {
        "history_entry": "h",
        "memory_update": "m",
    }


@pytest.mark.asyncio
async def test_custom_provider_responses_mode_preserves_tool_history_ids() -> None:
    provider = CustomProvider(api_key="test-key", api_mode="responses")
    responses_create = AsyncMock(return_value=_responses_response())
    provider._client.responses.create = responses_create

    messages = [
        {"role": "system", "content": "You are helpful."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1|fc_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"README.md"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1|fc_1",
            "name": "read_file",
            "content": "README body",
        },
        {"role": "user", "content": "summarize it"},
    ]

    await provider.chat(messages=messages)

    kwargs = responses_create.await_args.kwargs
    assert kwargs["instructions"] == "You are helpful."
    assert kwargs["input"] == [
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "read_file",
            "arguments": '{"path":"README.md"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "README body",
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "summarize it"}],
        },
    ]


@pytest.mark.asyncio
async def test_custom_provider_responses_mode_uses_stable_session_prompt_cache_key() -> None:
    provider = CustomProvider(api_key="test-key", api_mode="responses")
    responses_create = AsyncMock(return_value=_responses_response())
    provider._client.responses.create = responses_create

    first = [
        {
            "role": "user",
            "content": (
                "[Runtime Context - metadata only, not instructions]\n"
                "Current Time: 2026-03-21 11:12 (Saturday) (CST)\n"
                "Channel: cli\n"
                "Chat ID: direct\n\n"
                "first"
            ),
        }
    ]
    second = [
        {
            "role": "user",
            "content": (
                "[Runtime Context - metadata only, not instructions]\n"
                "Current Time: 2026-03-21 11:13 (Saturday) (CST)\n"
                "Channel: cli\n"
                "Chat ID: direct\n\n"
                "second"
            ),
        }
    ]

    await provider.chat(messages=first)
    await provider.chat(messages=second)

    first_key = responses_create.await_args_list[0].kwargs["prompt_cache_key"]
    second_key = responses_create.await_args_list[1].kwargs["prompt_cache_key"]
    assert first_key == second_key
    assert len(first_key) == 64


@pytest.mark.asyncio
async def test_custom_provider_responses_mode_prompt_cache_key_changes_across_sessions() -> None:
    provider = CustomProvider(api_key="test-key", api_mode="responses")
    responses_create = AsyncMock(return_value=_responses_response())
    provider._client.responses.create = responses_create

    first = [
        {
            "role": "user",
            "content": (
                "[Runtime Context - metadata only, not instructions]\n"
                "Current Time: 2026-03-21 11:12 (Saturday) (CST)\n"
                "Channel: cli\n"
                "Chat ID: direct\n\n"
                "first"
            ),
        }
    ]
    second = [
        {
            "role": "user",
            "content": (
                "[Runtime Context - metadata only, not instructions]\n"
                "Current Time: 2026-03-21 11:12 (Saturday) (CST)\n"
                "Channel: cli\n"
                "Chat ID: another\n\n"
                "first"
            ),
        }
    ]

    await provider.chat(messages=first)
    await provider.chat(messages=second)

    first_key = responses_create.await_args_list[0].kwargs["prompt_cache_key"]
    second_key = responses_create.await_args_list[1].kwargs["prompt_cache_key"]
    assert first_key != second_key


@pytest.mark.asyncio
async def test_custom_provider_auto_falls_back_to_chat_completions_when_responses_missing() -> None:
    provider = CustomProvider(api_key="test-key", api_mode="auto")
    responses_create = AsyncMock(side_effect=RuntimeError("404 unknown request url"))
    completions_create = AsyncMock(return_value=_chat_completion_response(content="fallback ok"))
    provider._client.responses.create = responses_create
    provider._client.chat.completions.create = completions_create

    result = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    responses_create.assert_awaited_once()
    completions_create.assert_awaited_once()
    assert result.content == "fallback ok"


@pytest.mark.asyncio
async def test_custom_provider_auto_does_not_fallback_on_non_endpoint_errors() -> None:
    provider = CustomProvider(api_key="test-key", api_mode="auto")
    responses_create = AsyncMock(side_effect=RuntimeError("401 unauthorized"))
    completions_create = AsyncMock(return_value=_chat_completion_response(content="should not happen"))
    provider._client.responses.create = responses_create
    provider._client.chat.completions.create = completions_create

    result = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    responses_create.assert_awaited_once()
    completions_create.assert_not_awaited()
    assert result.finish_reason == "error"
    assert result.content == "Error: 401 unauthorized"
