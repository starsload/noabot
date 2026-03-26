from types import SimpleNamespace

from nanobot.providers.base import ToolCallRequest
from nanobot.providers.litellm_provider import LiteLLMProvider


def test_litellm_parse_response_preserves_tool_call_provider_fields() -> None:
    provider = LiteLLMProvider(default_model="gemini/gemini-3-flash")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_123",
                            function=SimpleNamespace(
                                name="read_file",
                                arguments='{"path":"todo.md"}',
                                provider_specific_fields={"inner": "value"},
                            ),
                            provider_specific_fields={"thought_signature": "signed-token"},
                        )
                    ],
                ),
            )
        ],
        usage=None,
    )

    parsed = provider._parse_response(response)

    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].id == "call_123"
    assert parsed.tool_calls[0].provider_specific_fields == {"thought_signature": "signed-token"}
    assert parsed.tool_calls[0].function_provider_specific_fields == {"inner": "value"}


def test_tool_call_request_serializes_provider_fields() -> None:
    tool_call = ToolCallRequest(
        id="abc123xyz",
        name="read_file",
        arguments={"path": "todo.md"},
        provider_specific_fields={"thought_signature": "signed-token"},
        function_provider_specific_fields={"inner": "value"},
    )

    message = tool_call.to_openai_tool_call()

    assert message["provider_specific_fields"] == {"thought_signature": "signed-token"}
    assert message["function"]["provider_specific_fields"] == {"inner": "value"}
    assert message["function"]["arguments"] == '{"path": "todo.md"}'


def test_litellm_sanitize_messages_preserves_thought_signature_call_ids() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "gemini_call_123",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                    "provider_specific_fields": {"thought_signature": "signed-token"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "gemini_call_123",
            "name": "read_file",
            "content": "ok",
        },
    ]

    sanitized = LiteLLMProvider._sanitize_messages(messages)

    assert sanitized[0]["tool_calls"][0]["id"] == "gemini_call_123"
    assert sanitized[1]["tool_call_id"] == "gemini_call_123"


def test_litellm_sanitize_messages_preserves_camelcase_thought_signature_call_ids() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "gemini_call_456",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                    "provider_specific_fields": {"thoughtSignature": "signed-token"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "gemini_call_456",
            "name": "read_file",
            "content": "ok",
        },
    ]

    sanitized = LiteLLMProvider._sanitize_messages(messages)

    assert sanitized[0]["tool_calls"][0]["id"] == "gemini_call_456"
    assert sanitized[1]["tool_call_id"] == "gemini_call_456"


def test_litellm_parse_response_accepts_legacy_thought_signature_location() -> None:
    provider = LiteLLMProvider(default_model="gemini/gemini-3-flash")

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="legacy_call_1",
                            function=SimpleNamespace(
                                name="read_file",
                                arguments='{"path":"todo.md"}',
                                thought_signature="legacy-token",
                            ),
                        )
                    ],
                ),
            )
        ],
        usage=None,
    )

    parsed = provider._parse_response(response)

    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].id == "legacy_call_1"
    assert parsed.tool_calls[0].provider_specific_fields == {"thought_signature": "legacy-token"}
