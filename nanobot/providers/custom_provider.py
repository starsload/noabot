"""Direct OpenAI-compatible provider that can use chat or responses mode."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import json_repair
from openai import AsyncOpenAI

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.openai_responses import (
    convert_messages,
    convert_tools,
    parse_response_output,
)
from nanobot.utils.helpers import estimate_prompt_tokens as estimate_prompt_tokens_fallback


def _build_prompt_cache_key(messages: list[dict[str, Any]], model: str) -> str:
    """Build a deterministic cache key from messages and model."""
    content = model + "\n" + str(messages)
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def _convert_tool_choice_for_responses(tool_choice: str | dict[str, Any] | None) -> str | dict[str, Any] | None:
    """Normalize tool_choice for Responses API format."""
    if tool_choice is None or isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        fn = tool_choice.get("function") or {}
        name = tool_choice.get("name") or fn.get("name")
        if name:
            return {"type": "function", "name": name}
    return tool_choice


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
    _QWEN_REASONING_BUDGET_MAP = {
        "low": 1024,
        "medium": 4096,
        "high": 10_240,
    }
    _QWEN_REASONING_UNSUPPORTED_MODELS = (
        "qwen3-coder-plus",
        "qwen3-coder-next",
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

    @staticmethod
    def _is_qwen_model(model: str | None) -> bool:
        return isinstance(model, str) and "qwen" in model.lower()

    @classmethod
    def _supports_qwen_reasoning(cls, model: str | None) -> bool:
        if not cls._is_qwen_model(model):
            return False
        normalized = model.lower()
        return not any(marker in normalized for marker in cls._QWEN_REASONING_UNSUPPORTED_MODELS)

    @staticmethod
    def _strip_reasoning_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stripped: list[dict[str, Any]] = []
        changed = False
        for message in messages:
            if "reasoning_content" not in message and "thinking_blocks" not in message:
                stripped.append(message)
                continue
            stripped.append({
                key: value for key, value in message.items()
                if key not in {"reasoning_content", "thinking_blocks"}
            })
            changed = True
        return stripped if changed else messages

    def _resolve_qwen_thinking_budget(self, reasoning_effort: str | None) -> int | None:
        configured = self.generation.thinking_budget_tokens
        if isinstance(configured, int) and configured > 0:
            return configured
        if not reasoning_effort:
            return None
        return self._QWEN_REASONING_BUDGET_MAP.get(reasoning_effort.lower())

    def _qwen_reasoning_extra_body(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> dict[str, Any] | None:
        if not self._supports_qwen_reasoning(model):
            return None
        # DashScope enables Qwen deep-thinking via `enable_thinking`.
        budget = self._resolve_qwen_thinking_budget(reasoning_effort)
        if not reasoning_effort and budget is None:
            return None
        payload: dict[str, Any] = {"enable_thinking": True}
        if budget is not None:
            payload["thinking_budget"] = budget
        return payload

    def _prepare_request_messages(
        self,
        messages: list[dict[str, Any]],
        model_name: str,
    ) -> list[dict[str, Any]]:
        request_messages = self._sanitize_empty_content(messages)
        if self._supports_qwen_reasoning(model_name):
            request_messages = self._strip_reasoning_history(request_messages)
        if "gemini" in model_name.lower():
            request_messages = self._prune_gemini_unsigned_tool_history(request_messages)
        return request_messages

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
        request_messages = self._prepare_request_messages(messages, model_name)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": request_messages,
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }
        qwen_extra_body = self._qwen_reasoning_extra_body(model_name, reasoning_effort)
        if qwen_extra_body is not None:
            kwargs["extra_body"] = qwen_extra_body
        elif reasoning_effort:
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
        model_name = model or self.default_model
        instructions, input_items = convert_messages(
            self._prepare_request_messages(messages, model_name)
        )
        kwargs: dict[str, Any] = {
            "model": model_name,
            "input": input_items,
            "max_output_tokens": max(1, max_tokens),
            "prompt_cache_key": _build_prompt_cache_key(
                messages,
                model=model_name,
            ),
            "temperature": temperature,
        }
        if instructions:
            kwargs["instructions"] = instructions
        qwen_extra_body = self._qwen_reasoning_extra_body(model_name, reasoning_effort)
        if qwen_extra_body is not None:
            kwargs["extra_body"] = qwen_extra_body
        elif reasoning_effort:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        if tools:
            kwargs["tools"] = convert_tools(tools)
            kwargs["tool_choice"] = _convert_tool_choice_for_responses(tool_choice or "auto")
            kwargs["parallel_tool_calls"] = True
        response = await self._client.responses.create(**kwargs)
        return parse_response_output(response)

    def estimate_prompt_tokens(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> tuple[int, str]:
        model_name = model or self.default_model
        request_messages = self._prepare_request_messages(messages, model_name)
        return estimate_prompt_tokens_fallback(request_messages, tools), "custom_provider"

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
