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

    @staticmethod
    def _tool_call_has_thought_signature(tool_call: Any) -> bool:
        provider_fields = (
            tool_call.get("provider_specific_fields")
            if isinstance(tool_call, dict)
            else getattr(tool_call, "provider_specific_fields", None)
        )
        if isinstance(provider_fields, dict) and (
            provider_fields.get("thought_signature") or provider_fields.get("thoughtSignature")
        ):
            return True

        fn = tool_call.get("function") if isinstance(tool_call, dict) else getattr(tool_call, "function", None)
        if fn is None:
            return False
        return bool(
            getattr(tool_call, "thought_signature", None)
            or getattr(tool_call, "thoughtSignature", None)
            or (fn.get("thought_signature") if isinstance(fn, dict) else getattr(fn, "thought_signature", None))
            or (fn.get("thoughtSignature") if isinstance(fn, dict) else getattr(fn, "thoughtSignature", None))
        )

    @classmethod
    def _prune_gemini_unsigned_tool_history(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop assistant/tool pairs that lack thought_signature for Gemini tool turns."""
        dropped_ids: set[str] = set()
        repaired: list[dict[str, Any]] = []
        changed = False

        for message in messages:
            role = message.get("role")
            if role == "assistant" and isinstance(message.get("tool_calls"), list):
                tool_calls = message.get("tool_calls") or []
                kept_tool_calls = []
                for tc in tool_calls:
                    if cls._tool_call_has_thought_signature(tc):
                        kept_tool_calls.append(tc)
                    else:
                        tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                        if isinstance(tc_id, str) and tc_id:
                            dropped_ids.add(tc_id)
                        changed = True
                if kept_tool_calls:
                    if len(kept_tool_calls) != len(tool_calls):
                        repaired.append({**message, "tool_calls": kept_tool_calls})
                    else:
                        repaired.append(message)
                else:
                    content = message.get("content")
                    if content:
                        repaired.append({k: v for k, v in message.items() if k != "tool_calls"})
                    else:
                        changed = True
                continue

            if role == "tool":
                call_id = message.get("tool_call_id")
                if isinstance(call_id, str) and call_id in dropped_ids:
                    changed = True
                    continue

            repaired.append(message)

        return repaired if changed else messages

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
        model_name = model or self.default_model
        request_messages = self._sanitize_empty_content(messages)
        if "gemini" in model_name.lower():
            request_messages = self._prune_gemini_unsigned_tool_history(request_messages)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": request_messages,
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
        def _get(obj: Any, key: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

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
        tool_calls: list[ToolCallRequest] = []
        for tc in (msg.tool_calls or []):
            fn = _get(tc, "function", {}) or {}
            arguments = _get(fn, "arguments")
            if isinstance(arguments, str):
                arguments = json_repair.loads(arguments)

            provider_specific_fields = _get(tc, "provider_specific_fields") or None
            function_provider_specific_fields = _get(fn, "provider_specific_fields") or None

            # Compatibility with adapters that expose thought_signature on the
            # function/tool-call object instead of provider_specific_fields.
            if not provider_specific_fields:
                thought_signature = (
                    _get(tc, "thought_signature")
                    or _get(tc, "thoughtSignature")
                    or _get(fn, "thought_signature")
                    or _get(fn, "thoughtSignature")
                )
                if thought_signature:
                    provider_specific_fields = {"thought_signature": thought_signature}

            tool_calls.append(ToolCallRequest(
                id=_get(tc, "id", ""),
                name=_get(fn, "name", ""),
                arguments=arguments,
                provider_specific_fields=provider_specific_fields,
                function_provider_specific_fields=function_provider_specific_fields,
            ))
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
