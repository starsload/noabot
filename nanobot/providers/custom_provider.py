"""Direct OpenAI-compatible provider that can use chat or responses mode."""

from __future__ import annotations

import uuid
from typing import Any

import json_repair
from openai import AsyncOpenAI

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.openai_responses import (
    build_prompt_cache_key,
    convert_messages_to_responses_input,
    convert_tool_choice_to_responses,
    convert_tools_to_responses_tools,
    parse_responses_api_response,
)


class CustomProvider(LLMProvider):
    _RESPONSES_FALLBACK_MARKERS = (
        "404",
        "not found",
        "unsupported",
        "unknown request url",
        "unrecognized request url",
        "no route",
        "method not allowed",
    )

    def __init__(
        self,
        api_key: str = "no-key",
        api_base: str = "http://localhost:8000/v1",
        default_model: str = "default",
        extra_headers: dict[str, str] | None = None,
        api_mode: str = "chat_completions",
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.api_mode = api_mode
        # Keep affinity stable for this provider instance to improve backend cache locality,
        # while still letting users attach provider-specific headers for custom gateways.
        default_headers = {
            "x-session-affinity": uuid.uuid4().hex,
            **(extra_headers or {}),
        }
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
            default_headers=default_headers,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        try:
            if self.api_mode == "responses":
                return await self._chat_via_responses(
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                    tool_choice=tool_choice,
                )
            if self.api_mode == "auto":
                try:
                    return await self._chat_via_responses(
                        messages=messages,
                        tools=tools,
                        model=model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        reasoning_effort=reasoning_effort,
                        tool_choice=tool_choice,
                    )
                except Exception as exc:
                    if not self._should_fallback_to_chat_completions(exc):
                        raise
            return await self._chat_via_completions(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                tool_choice=tool_choice,
            )
        except Exception as exc:
            return LLMResponse(content=f"Error: {exc}", finish_reason="error")

    async def _chat_via_completions(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": self._sanitize_empty_content(messages),
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        if tools:
            kwargs.update(tools=tools, tool_choice=tool_choice or "auto")
        response = await self._client.chat.completions.create(**kwargs)
        return self._parse_chat_completions(response)

    async def _chat_via_responses(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        instructions, input_items = convert_messages_to_responses_input(
            self._sanitize_empty_content(messages)
        )
        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "input": input_items,
            "max_output_tokens": max(1, max_tokens),
            "prompt_cache_key": build_prompt_cache_key(
                messages,
                model=model or self.default_model,
            ),
            "temperature": temperature,
        }
        if instructions:
            kwargs["instructions"] = instructions
        if reasoning_effort:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        if tools:
            kwargs["tools"] = convert_tools_to_responses_tools(tools)
            kwargs["tool_choice"] = convert_tool_choice_to_responses(tool_choice or "auto")
            kwargs["parallel_tool_calls"] = True
        response = await self._client.responses.create(**kwargs)
        return parse_responses_api_response(response)

    @classmethod
    def _should_fallback_to_chat_completions(cls, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(marker in text for marker in cls._RESPONSES_FALLBACK_MARKERS)

    def _parse_chat_completions(self, response: Any) -> LLMResponse:
        if not response.choices:
            return LLMResponse(
                content=(
                    "Error: API returned empty choices. This may indicate a temporary "
                    "service issue or an invalid model response."
                ),
                finish_reason="error",
            )
        choice = response.choices[0]
        msg = choice.message
        tool_calls = [
            ToolCallRequest(
                id=tc.id,
                name=tc.function.name,
                arguments=(
                    json_repair.loads(tc.function.arguments)
                    if isinstance(tc.function.arguments, str)
                    else tc.function.arguments
                ),
            )
            for tc in (msg.tool_calls or [])
        ]
        usage = response.usage
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=(
                {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                }
                if usage
                else {}
            ),
            reasoning_content=getattr(msg, "reasoning_content", None) or None,
        )

    def get_default_model(self) -> str:
        return self.default_model
